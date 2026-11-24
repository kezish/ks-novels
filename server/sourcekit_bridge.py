"""Optional local SourceKit subprocess integration; never exposes the engine API."""
import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from fastapi import HTTPException

BIN=os.environ.get("NOVELS_SOURCEKIT_BIN","")
USE_52=os.environ.get("NOVELS_SOURCEKIT_52","0")=="1"
SITES={"sk_shuku52":"52书库","sk_biquge5":"笔趣阁5","sk_czbooks":"小说狂人"}
SEARCH_LIMIT=asyncio.Semaphore(2)

def classify(url):
    u=urlsplit(url)
    if u.scheme!="https" or u.username is not None or u.password is not None or u.port not in (None,443) or u.query:
        raise ValueError("仅支持 HTTPS 小说主页")
    host=u.hostname
    if host in ("www.52shuku.net","52shuku.net"):
        return "sk_shuku52"
    if host=="www.biquge5.com" and re.fullmatch(r"/[\d_]+/?",u.path):
        return "sk_biquge5"
    if host=="czbooks.net" and re.fullmatch(r"/n/[A-Za-z0-9_-]{1,100}/?",u.path):
        return "sk_czbooks"
    raise ValueError("不支持的书源或链接")

def normalize_external(url):
    if not BIN:raise ValueError("SourceKit 尚未启用")
    site=classify(url)
    if site=="sk_shuku52":raise ValueError("52书库由原有URL校验处理")
    u=urlsplit(url)
    return urlunsplit(("https",u.hostname,u.path,"",""))

def command(url,books,data):
    site=classify(url)
    if not BIN or (site=="sk_shuku52" and not USE_52):
        return None
    return [BIN,"download",url,"--output-dir",str(books),"--cache-dir",str(data/"sourcekit-cache")]

async def search(keyword,sites):
    if not BIN:raise HTTPException(503,"SourceKit 尚未启用")
    if not keyword.strip() or len(keyword)>100 or not sites or len(sites)>3 or any(s not in SITES for s in sites):
        raise HTTPException(422,"无效搜索参数")
    async with SEARCH_LIMIT:
        proc=await asyncio.create_subprocess_exec(BIN,"search",keyword,"--sites",*dict.fromkeys(sites),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            out,err=await asyncio.wait_for(proc.communicate(),timeout=30)
        except (asyncio.TimeoutError,asyncio.CancelledError):
            if proc.returncode is None:proc.kill()
            await proc.wait()
            raise
        if proc.returncode!=0 or len(out)>1024*1024:
            raise HTTPException(502,"书源搜索服务失败")
        try:
            data=json.loads(out)
            if not isinstance(data["results"],list) or not isinstance(data["statuses"],dict):raise ValueError()
        except (KeyError,TypeError,ValueError) as e:
            raise HTTPException(502,"书源响应无效") from e
        # Only URLs for the declared book sources enter the browser/queue.
        rows=[]
        for item in data["results"]:
            try:
                if classify(item["book_url"])!=item["site"] or item["site"] not in sites:continue
                if item["site"]=="sk_shuku52" and not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\.html",urlsplit(item["book_url"]).path):continue
                rows.append({k:str(item.get(k,""))[:1200] for k in ("site","title","author","book_url")})
            except (ValueError,KeyError,TypeError):continue
        return {"results":rows,"statuses":{s:data["statuses"].get(s,{"state":"unknown"}) for s in sites}}
