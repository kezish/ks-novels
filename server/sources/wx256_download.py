"""Export accessible, authorized reading pages as UTF-8 TXT through the job queue."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request
from .base import HTTP, Node, parse, clean
from .wx256 import BASE, book_url

MAX_PAGES=5000
MAX_TEXT=40_000_000

def node_id(root, tag, id):
    return next((n for n in root.walk() if n.tag==tag and n.attrs.get("id")==id), Node())

def page_number(value, book):
    url=urljoin(book,value)
    u=urlsplit(url)
    path=urlsplit(book).path
    m=re.fullmatch(re.escape(path)+r"([1-9]\d*)\.html",u.path)
    if not m or u.scheme!="https" or u.hostname!="www.256wx.org" or u.port not in (None,443) or u.username is not None or u.password is not None or u.query or u.fragment:
        raise ValueError("256目录含无效阅读页链接")
    return int(m[1])

def read_directory(body, book):
    root=parse(body)
    title=clean(node_id(root,"h1","nr_title").text())
    catalog=node_id(root,"ul","nr1")
    if not title or not catalog.tag:
        raise ValueError("256详情页结构无效")
    numbers=[]
    for a in catalog.find("a"):
        if not a.attrs.get("href"):continue
        n=page_number(a.attrs["href"],book)
        label=clean(a.text())
        if not re.fullmatch(r"第\s*"+str(n)+r"\s*页",label):
            raise ValueError("256目录页码与标题不一致")
        numbers.append(n)
    if not numbers or len(numbers)>MAX_PAGES or numbers!=list(range(1,len(numbers)+1)):
        raise ValueError("256目录有缺页、重复或乱序")
    return title,len(numbers)

def content_text(node):
    if node.tag in ("script","style","noscript","ul","iframe","ins") or any(node.has(c) for c in ("page","pagination","pagination2","ad","ads")):
        return ""
    if node.tag=="br":return "\n"
    return "".join(content_text(c) if isinstance(c,Node) else c for c in node.children)

def read_page(body,book,title,n,total):
    root=parse(body)
    heading=clean(node_id(root,"h1","nr_title").text())
    if heading!=f"{title}({n})" and heading!=f"{title}（{n}）":
        raise ValueError(f"256第{n}页标题与目录不一致")
    node=node_id(root,"div","nr1")
    if not node.has("book_con"):raise ValueError(f"256第{n}页正文容器缺失")
    # Use the explicit reader navigation as an independent check of directory size.
    nav={}
    for a in node.find("a"):
        label=clean(a.text())
        if label in ("上一页","下一页","尾页"):
            num=page_number(a.attrs.get("href",""),book)
            if label in nav and nav[label]!=num:raise ValueError("256阅读页导航冲突")
            nav[label]=num
    if n<total and nav.get("下一页")!=n+1:
        raise ValueError(f"256第{n}页下一页缺失或不连续")
    if n==total and "下一页" in nav:
        raise ValueError("256目录尾页与阅读页不一致")
    if n>1 and nav.get("上一页")!=n-1:
        raise ValueError(f"256第{n}页上一页不连续")
    if "尾页" in nav and nav["尾页"]!=total:
        raise ValueError("256目录尾页不一致")
    lines=[]
    for p in node.find("p"):
        text=clean(content_text(p))
        if not text:continue
        text=re.sub(r"【256文学将分享[^】]*】","",text).strip()
        if text:lines.append(text)
    if not lines:raise ValueError(f"256第{n}页没有有效正文")
    return "\n\n".join(lines)

class Reader:
    def __init__(self):
        self.http=HTTP({"www.256wx.org"},interval=3)
    def get(self,url):
        self.http.safe(url)
        for attempt in range(3):
            time.sleep(max(0,self.http.interval-(time.monotonic()-self.http.last)))
            self.http.last=time.monotonic()
            try:
                with self.http.opener.open(Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; personal novel catalog)","Accept":"text/html"}),timeout=20) as r:
                    if r.url.rstrip("/")!=url.rstrip("/"):raise ValueError("256阅读地址被重定向")
                    raw=r.read(3_000_001)
                    if len(raw)>3_000_000:raise ValueError("256响应过大")
                    return raw.decode("utf-8",errors="strict")
            except HTTPError as e:
                if e.code not in (500,502,503,504) or attempt==2:raise
            except (TimeoutError,URLError):
                if attempt==2:raise
            time.sleep(3*(attempt+1))
        raise RuntimeError("256读取失败")

def export(book,out,reader=None,progress=None):
    book=book_url(book)
    reader=reader or Reader()
    progress=progress or (lambda done,total,failed:None)
    title,total=read_directory(reader.get(book),book)
    progress(0,total,0)
    pieces=[]
    hashes=set()
    size=0
    for n in range(1,total+1):
        try:
            text=read_page(reader.get(book+str(n)+".html"),book,title,n,total)
            data=text.encode("utf-8")
            digest=hashlib.sha256(data).digest()
            if digest in hashes:raise ValueError(f"256第{n}页内容重复")
            size+=len(data)+2
            if size>MAX_TEXT:raise ValueError("256导出超过40MB上限")
        except Exception:
            progress(n-1,total,1)
            raise
        pieces.append(data)
        hashes.add(digest)
        progress(n,total,0)
    out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    safe=re.sub(r'[\x00-\x1f<>:"/\\|?*]',"_",title).strip(" .") or "256小说"
    safe=safe.encode("utf-8")[:180].decode("utf-8","ignore")
    final=out/(safe+"-wx256"+book.split("/")[-2]+".txt")
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(dir=out,prefix=".wx256-",suffix=".part",delete=False) as f:
            tmp=Path(f.name)
            f.write(b"\n\n".join(pieces)+b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,final)
    finally:
        if tmp is not None:tmp.unlink(missing_ok=True)
    return final

def main():
    def interrupt(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupt)
    p=argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--output-dir",required=True)
    args=p.parse_args()
    try:
        final=export(args.url,args.output_dir,progress=lambda done,total,failed:print(f"downloading: {done}/{total} completed, {failed} failed",file=sys.stderr,flush=True))
        print(final,flush=True)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"256下载失败：{str(e)[:300]}",file=sys.stderr,flush=True)
        return 1
if __name__=="__main__":raise SystemExit(main())
