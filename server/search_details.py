"""Lazy source-specific introductions."""
import asyncio
import json
import re
import time
from collections import OrderedDict
from urllib.parse import urlsplit
from fastapi import HTTPException
import sourcekit_bridge as bridge
from sources import wx256

CACHE=OrderedDict()
LOCKS={s:asyncio.Lock() for s in bridge.ALL_SITES}
LIMIT=asyncio.Semaphore(2)
LAST={}

def identity(site,url):
    try:
        u=urlsplit(url)
        if u.scheme!="https" or u.username or u.password or u.port not in (None,443) or u.query:
            raise ValueError()
        if site==wx256.SITE:
            canonical=wx256.book_url(url)
            return wx256.SITE,canonical.split("/")[-2],canonical
        if site=="sk_jiujiu" and u.hostname=="www.ijjxsxzw.com":
            if m:=re.fullmatch(r"/txt/([0-9]{1,12})\.html",u.path):
                return "jiujiu",m[1],"https://www.ijjxsxzw.com"+u.path
        elif site in bridge.SITES and bridge.classify(url)==site:
            if site=="sk_shuku52":
                if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\.html",u.path) or re.search(r"_\d+\.html$",u.path):
                    raise ValueError()
                return "52shuku",u.path,"https://www.52shuku.net"+u.path
            path=u.path.rstrip("/") + ("/" if site=="sk_biquge5" else "")
            return site,path,"https://"+u.hostname+path
    except (ValueError,TypeError):
        pass
    raise HTTPException(422,"无效书籍链接")

def catalog_lookup(path,source,external_id):
    if source not in ("52shuku","jiujiu") or not path.exists():return None
    from catalog.store import connect
    with connect(path) as db:
        row=db.execute("SELECT title,author,description FROM book_sources WHERE source=? AND external_id=?",(source,external_id)).fetchone()
    if row and row["description"].strip():
        return {**dict(row),"kind":"catalog"}
    return None

def read52(url):
    from sources.shuku52 import Shuku52,parse_frontmatter,parse_metadata
    from sources.base import Entry
    from catalog.store import now
    adapter=Shuku52()
    intro=parse_frontmatter(adapter.http.get(url[:-5]+"_2.html"))
    if intro:
        return {"description":intro[:30000],"kind":"full"}
    entry=Entry(urlsplit(url).path,url,"","","",now().date().isoformat(),"latest_upload")
    meta=parse_metadata(adapter.http.get(url),entry)
    return {"title":meta.title,"author":meta.author,"description":meta.description[:30000],"kind":"excerpt"}

async def read_engine(url):
    proc=await asyncio.create_subprocess_exec(bridge.BIN,"metadata",url,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        out,err=await asyncio.wait_for(proc.communicate(),timeout=18)
    except (asyncio.TimeoutError,asyncio.CancelledError):
        if proc.returncode is None:proc.kill()
        await proc.wait()
        raise
    if proc.returncode!=0:
        message=err.decode("utf-8","replace")
        if "HTTP 429" in message:raise HTTPException(429,"原站限流，请稍后重试")
        if "浏览器验证" in message:raise HTTPException(502,"原站要求浏览器验证")
        raise HTTPException(502,"原站简介暂不可用")
    if len(out)>512000:raise HTTPException(502,"简介响应过大")
    try:
        data=json.loads(out)
        result={k:str(data.get(k,""))[:30000] for k in ("title","author","description")}
        result["kind"]="site"
        return result
    except (ValueError,TypeError,AttributeError) as e:
        raise HTTPException(502,"简介响应异常") from e

async def detail(site,url,path):
    source,external_id,canonical=identity(site,url)
    cached=await asyncio.to_thread(catalog_lookup,path,source,external_id)
    if cached:return cached
    if site=="sk_jiujiu":raise HTTPException(404,"本地书单中未找到简介")
    if not bridge.BIN:raise HTTPException(503,"SourceKit 尚未启用")
    key=(site,canonical)
    async with LOCKS[site]:
        entry=CACHE.get(key)
        if entry and time.monotonic()-entry[0]<86400:
            CACHE.move_to_end(key)
            return entry[1]
        async with LIMIT:
            await asyncio.sleep(max(0,2.5-(time.monotonic()-LAST.get(site,0))))
            LAST[site]=time.monotonic()
            try:
                if site==wx256.SITE:data=await asyncio.to_thread(wx256.read_intro,canonical)
                elif site=="sk_shuku52":data=await asyncio.to_thread(read52,canonical)
                else:data=await read_engine(canonical)
            except HTTPException:raise
            except Exception as e:
                if getattr(e,"code",None)==429:raise HTTPException(429,"原站限流，请稍后重试") from e
                raise HTTPException(502,"原站简介暂不可用") from e
        if data.get("description"):
            CACHE[key]=(time.monotonic(),data);CACHE.move_to_end(key)
            while len(CACHE)>512:CACHE.popitem(last=False)
        return data
