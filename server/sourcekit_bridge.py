"""Optional local SourceKit subprocess integration; never exposes the engine API."""
import asyncio
import json
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from fastapi import HTTPException
from sources import wx256

BIN=os.environ.get("NOVELS_SOURCEKIT_BIN","")
USE_52=os.environ.get("NOVELS_SOURCEKIT_52","0")=="1"
SITES={"sk_shuku52":"52书库","sk_biquge5":"笔趣阁5","sk_czbooks":"小说狂人"}
SEARCH_LIMIT=asyncio.Semaphore(2)
CATALOG_SITE="sk_jiujiu"
ALL_SITES={**SITES,CATALOG_SITE:"久久小说（本地书单）",wx256.SITE:"256文学"}
CATEGORIES={"romance":"言情","bl":"耽美","gl":"百合","fanfic":"综漫","male":"男频","ncp":"无CP"}
# Local catalog categories are not interchangeable with relationship tags.
LOCAL_CATEGORIES={"ncp":("无CP向",),"romance":("现代言情","总裁豪门"),"fanfic":("同人小说",)}
def date_value(value):
    try:return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:return ""


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

async def search_live(keyword,sites,cursors=None,category="",order="relevance"):
    if not BIN:raise HTTPException(503,"SourceKit 尚未启用")
    if not keyword.strip() or len(keyword)>100 or not sites or len(sites)>3 or any(s not in SITES for s in sites):
        raise HTTPException(422,"无效搜索参数")
    async with SEARCH_LIMIT:
        args=[BIN,"search","--sites",*dict.fromkeys(sites)]
        if cursors:args.extend(["--cursors",json.dumps(cursors)])
        if category:args.extend(["--category",category])
        if order!="relevance":args.extend(["--order",order])
        args.extend(["--",keyword])
        proc=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            out,err=await asyncio.wait_for(proc.communicate(),timeout=40)
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
                row={k:str(item.get(k,""))[:1200] for k in ("site","title","author","book_url")}
                row["category"]=str(item.get("category") or "")[:100]
                row["published"]=date_value(item.get("published",""))
                rows.append(row)
            except (ValueError,KeyError,TypeError):continue
        statuses={}
        for site in sites:
            st=dict(data["statuses"].get(site,{"state":"unknown"}))
            st["count"]=sum(r["site"]==site for r in rows)
            statuses[site]=st
        return {"results":rows,"statuses":statuses}

def search_catalog(keyword,path,page=1,category="",order="relevance"):
    """Literal local catalog search, not an equivalent of the full site search."""
    from catalog.store import connect,init
    from catalog.dedup import normalize
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    init(path)
    term=normalize(keyword)
    if not term:
        return []
    with connect(path) as db:
        # Normalize in the query to preserve Unicode title/author matching.
        db.create_function("novel_norm",1,normalize,deterministic=True)
        where="";params=[term,term]
        if category:
            cats=LOCAL_CATEGORIES.get(category,())
            if not cats:return []
            where=" AND category IN ("+",".join("?" for _ in cats)+")"
            params.extend(cats)
        if order=="relevance":
            sorting="CASE WHEN novel_norm(title)=? THEN 0 ELSE 1 END,uploaded DESC,external_id"
            params.append(term)
        else:
            sorting="CASE WHEN uploaded='' THEN 1 ELSE 0 END,uploaded "+("DESC" if order=="newest" else "ASC")+",external_id"
        rows=db.execute("""SELECT book_id,external_id,title,author,url,category,uploaded
            FROM book_sources WHERE source='jiujiu' AND
            (instr(novel_norm(title),?)>0 OR instr(novel_norm(author),?)>0)"""+where+" ORDER BY "+sorting+" LIMIT 101 OFFSET ?",
            (*params,(page-1)*100)).fetchall()
    from urllib.parse import urlencode
    result=[]
    for row in rows:
        if not re.fullmatch(r"https://www\.ijjxsxzw\.com/txt/[0-9]+\.html",row["url"]):
            continue
        direct=f"/api/books/{row['book_id']}/direct?"+urlencode({"source":"jiujiu","external_id":row["external_id"]})
        result.append(dict(site=CATALOG_SITE,title=row["title"],author=row["author"],book_url=row["url"],direct_url=direct,category=row["category"],published=date_value(row["uploaded"])))
    return result

async def search(keyword,sites,catalog_path=None,cursors=None,category="",order="relevance"):
    cursors=cursors or {}
    if any(s not in sites or type(n) is not int or not 1<=n<=10000 for s,n in cursors.items()):
        raise HTTPException(422,"无效搜索页码")
    if not BIN:raise HTTPException(503,"SourceKit 尚未启用")
    if not keyword.strip() or len(keyword)>100 or not sites or len(sites)>len(ALL_SITES) or any(s not in ALL_SITES for s in sites):
        raise HTTPException(422,"无效搜索参数")
    if (category and category not in CATEGORIES) or order not in ("relevance","newest","oldest"):
        raise HTTPException(422,"无效筛选或排序")
    eligible=[s for s in sites if not category or (s=="sk_shuku52" and category!="ncp") or (s==CATALOG_SITE and category in LOCAL_CATEGORIES)]
    skipped={s:{"state":"unsupported","count":0,"message":"该书源暂不支持此类型筛选"} for s in sites if s not in eligible}
    live=[s for s in eligible if s in SITES]
    result={"results":[],"statuses":{}}
    if live:
        try:result=await search_live(keyword,live,{s:n for s,n in cursors.items() if s in live},category,order)
        except (HTTPException,OSError,asyncio.TimeoutError):
            if CATALOG_SITE not in sites and wx256.SITE not in sites:raise
            result["statuses"]={s:{"state":"failed"} for s in live}
    if CATALOG_SITE in eligible:
        if catalog_path is None:raise HTTPException(503,"本地书单不可用")
        page=cursors.get(CATALOG_SITE,1)
        rows=await asyncio.to_thread(search_catalog,keyword,catalog_path,page,category,order)
        more=len(rows)>100
        rows=rows[:100]
        result["results"].extend(rows)
        result["statuses"][CATALOG_SITE]={"state":"catalog","count":len(rows),"pages":1,"next_page":page+1 if more else None,"message":"原站搜索暂不可用；仅查询已同步书单"}
    if wx256.SITE in eligible:
        rows,status=await asyncio.to_thread(wx256.SEARCHER.search,keyword,cursors.get(wx256.SITE,1))
        result["results"].extend(rows)
        result["statuses"][wx256.SITE]=status
    result["statuses"].update(skipped)
    if order!="relevance":
        dated=[r for r in result["results"] if r.get("published")]
        unknown=[r for r in result["results"] if not r.get("published")]
        result["results"]=sorted(dated,key=lambda r:r["published"],reverse=order=="newest")+unknown
    return result
