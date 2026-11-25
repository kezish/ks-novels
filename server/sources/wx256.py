"""256文学 live public metadata search; independent of the daily catalog."""
import re
import time
from collections import OrderedDict
from threading import Lock
from urllib.parse import urljoin,urlsplit,urlencode,parse_qs
from urllib.request import Request
from urllib.error import HTTPError
from .base import HTTP,parse,first,clean,date_time,Node

BASE="https://www.256wx.org"
SITE="sk_wx256"
SEARCH=BASE+"/e/search/"

def book_url(value):
    u=urlsplit(urljoin(BASE+"/",value))
    m=re.fullmatch(r"/read/([0-9]{1,12})/?",u.path)
    if u.scheme!="https" or u.hostname!="www.256wx.org" or u.port not in (None,443) or u.username or u.password or u.query or not m:
        raise ValueError("无效256书籍链接")
    return BASE+"/read/"+m[1]+"/"

def result_identity(value):
    u=urlsplit(value)
    q=parse_qs(u.query,strict_parsing=True)
    if u.scheme!="https" or u.hostname!="www.256wx.org" or u.port not in (None,443) or u.username or u.password or u.path not in ("/e/search/result/","/e/search/result/index.php") or set(q)-{"searchid","page"}:
        raise ValueError("256搜索响应地址异常")
    sid=q.get("searchid",[])
    pages=q.get("page",["0"])
    if len(sid)!=1 or not re.fullmatch(r"\d{1,15}",sid[0]) or len(pages)!=1 or not re.fullmatch(r"\d{1,5}",pages[0]):
        raise ValueError("256搜索分页参数异常")
    return sid[0],int(pages[0])+1

def parse_results(body,url,keyword):
    sid,page=result_identity(url)
    root=parse(body)
    main=first(root,cls="main_left")
    heading=first(main,cls="position").text()
    if not main.tag or not re.search(r'\d+\s*项符合["“]'+re.escape(keyword)+r'["”]的查询结果',heading):
        raise ValueError("256未返回有效搜索结果页")
    rows=[]
    seen=set()
    for card in main.find("header"):
        link=first(first(card,"h3"),"a")
        try:
            target=book_url(link.attrs.get("href",""))
        except ValueError:
            continue
        title,sep,author=link.text().strip().rpartition(" 作者：")
        if not sep:title,author=link.text().strip(),""
        if not title or target in seen:continue
        seen.add(target)
        try:published=date_time(first(card,"time").text())[:10]
        except ValueError:published=""
        rows.append(dict(site=SITE,title=title.strip(),author=author.strip(),book_url=target,published=published,category=""))
    next_url=None
    for a in first(main,cls="page").find("a"):
        if a.text().strip()!="下一页":continue
        target=urljoin(url,a.attrs.get("href",""))
        if result_identity(target)==(sid,page+1):next_url=target
    if not rows and not re.search(r"(?:没有|未找到|无相关|0\s*[条项])",main.text()+" "+heading):
        raise ValueError("256搜索结果为空或结构异常")
    return rows,next_url

class WXSearch:
    def __init__(self):
        self.http=HTTP({"www.256wx.org"},interval=3)
        self.lock=Lock()
        self.sessions=OrderedDict()
        self.last_submit=None
    def request(self,url,data=None):
        self.http.safe(url)
        time.sleep(max(0,self.http.interval-(time.monotonic()-self.http.last)))
        self.http.last=time.monotonic()
        req=Request(url,data=data,headers={"User-Agent":"Mozilla/5.0 (compatible; personal novel catalog)","Accept":"text/html"})
        with self.http.opener.open(req,timeout=12) as r:
            self.http.safe(r.url)
            raw=r.read(3_000_001)
            if len(raw)>3_000_000:raise ValueError("256响应过大")
            return raw.decode("utf-8","replace"),r.url
    def search(self,keyword,page=1):
        with self.lock:
            return self._search(keyword,page)
    def _search(self,keyword,page):
        now=time.monotonic()
        session=self.sessions.get(keyword)
        if session and now-session["created"]>900:
            self.sessions.pop(keyword,None);session=None
        if session and page in session["pages"]:
            self.sessions.move_to_end(keyword)
            return session["pages"][page]
        def failure(state,msg,retry=None):
            return [],{"state":state,"count":0,"message":msg,"next_page":retry}
        if page>1 and (not session or page not in session["links"]):
            return failure("failed","256搜索会话已失效，请重新搜索")
        try:
            if page==1:
                # EmpireCMS can reject rapid new searches. Honor a conservative cooldown.
                if self.last_submit is not None and now-self.last_submit<30:
                    return failure("throttled","256原站搜索间隔限制，请稍后继续加载",1)
                self.last_submit=now
                body,url=self.request(SEARCH,urlencode({"show":"title,writer","tempid":"1","tbname":"article","keyboard":keyword}).encode())
                if urlsplit(url).path=="/e/search/":
                    text=clean(parse(body).text())
                    if re.search(r"间隔|频繁|稍后|等待|秒后",text):
                        return failure("throttled","256原站限制搜索频率，请稍后继续加载",1)
                    if re.search(r"没有.*(?:信息|结果|记录)|未找到|无相关|找不到",text):
                        return [],{"state":"completed","count":0,"next_page":None}
                    return failure("failed","256原站返回信息提示，未获得有效结果")
                session={"created":now,"links":{},"pages":{}}
            else:
                body,url=self.request(session["links"][page])
                if result_identity(url)!=result_identity(session["links"][page]):
                    raise ValueError("256分页地址不一致")
            if result_identity(url)[1]!=page:raise ValueError("256页码不一致")
            rows,nxt=parse_results(body,url,keyword)
        except HTTPError as e:
            return failure("throttled" if e.code==429 else "failed",f"256原站 HTTP {e.code}",page if e.code==429 else None)
        except (OSError,ValueError):
            return failure("failed","256原站响应异常，请重试")
        if nxt:session["links"][page+1]=nxt
        result=(rows,{"state":"completed","count":len(rows),"pages":1,"next_page":page+1 if nxt else None})
        session["pages"][page]=result
        self.sessions[keyword]=session
        self.sessions.move_to_end(keyword)
        while len(self.sessions)>32:self.sessions.popitem(last=False)
        return result

SEARCHER=WXSearch()

def parse_intro(body):
    root=parse(body)
    content=next((n for n in root.walk() if n.attrs.get("id")=="nr1" and n.has("book_con")),Node())
    lines=[]
    for p in content.find("p"):
        text=clean(p.text())
        if re.match(r"^第\s*[0-9零〇一二三四五六七八九十百千两]+\s*[章节回卷](?:\s|[：:、.。]|$)",text):
            return "\n\n".join(lines).strip()
        if text and not re.fullmatch(r"[=—－_\s]{3,}",text):
            lines.append(text)
    return ""

def read_intro(url):
    url=book_url(url)
    http=HTTP({"www.256wx.org"})
    intro=parse_intro(http.get(url+"1.html"))
    if intro:return {"description":intro[:30000],"kind":"full"}
    root=parse(http.get(url))
    excerpt=next((n for n in root.walk() if n.attrs.get("id")=="nr_history"),Node())
    text=clean(excerpt.text())
    if not text:raise ValueError("256简介未找到")
    return {"description":text[:30000],"kind":"excerpt"}
