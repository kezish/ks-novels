import re
from urllib.parse import urlsplit, urljoin
from dataclasses import replace
from .base import Entry, Metadata, HTTP, parse, first, clean, date_time

BASE='https://www.52shuku.net'
def split_title(raw):
    raw = re.sub(r'【(?:完结|正文完|番外)[^】]*】\s*$','',raw.strip())
    title, sep, author = raw.rpartition('_')
    return (title.strip(),author.strip()) if sep else (raw,'')
def parse_list(html):
    root = parse(html)
    entries=[]
    for article in root.find('article','excerpt'):
        a = first(first(article,'h2'),'a')
        url = a.attrs.get('href','')
        path = urlsplit(url).path
        if not re.fullmatch(r'/yanqing/[\w-]+/[\w-]+\.html',path) or urlsplit(url).hostname != 'www.52shuku.net':
            continue
        title,author=split_title(a.text())
        note=first(article,cls='note').text().strip()
        m=re.match(r'\[([^\]]+)\]',note)
        category=m[1] if m else ''
        entries.append(Entry(path,BASE+path,title,author,category,date_time(first(article,cls='muted').text()),'latest_upload'))
    if not entries: raise ValueError('52榜单为空或页面结构异常')
    return entries, any(a.text().strip()=='下一页' for a in root.find('a'))

def parse_metadata(html, entry):
    root=parse(html)
    article=first(root,'article','article-content')
    paragraphs=[]
    category=entry.category
    for p in article.find('p'):
        text=clean(p.text())
        if text.startswith('所属专题'):
            if not category:
                category=first(p,'a').text().strip()
            break
        if text.startswith(('Tips：','Tips:','开始阅读')): break
        if text and text not in ('小说简介：','小说简介:'):
            paragraphs.append(text)
    if not paragraphs: raise ValueError('52简介未找到')
    title,author=split_title(first(root,'h1','article-title').text())
    stamp=first(root,'time').text()
    return Metadata(title or entry.title,author or entry.author,category or '未分类','\n\n'.join(paragraphs),date_time(stamp or entry.uploaded))

def parse_frontmatter(html):
    root=parse(html)
    body=next((n for n in root.walk() if n.attrs.get('id') in ('text','nr1')),None) or first(root,'article','article-content')
    if body is None: raise ValueError('52阅读页正文容器未找到')
    lines=[]
    for p in body.find('p'):
        line=clean(p.text())
        if re.match(r'^第\s*[0-9零一二三四五六七八九十百千两〇]+\s*[章节回卷]',line):
            return '\n\n'.join(lines).strip()
        if line and not re.fullmatch(r'[=—－_\s]{3,}',line):
            lines.append(line)
    # 不将未识别边界的整页正文当作简介。
    return ''

class Shuku52:
    source_id='52shuku'
    display_name = '52书库'
    download_kind = 'queued'
    modes=('latest_upload',)
    def __init__(self):
        self.http=HTTP({'www.52shuku.net','52shuku.net'})
    def page(self, mode, page):
        url=BASE+'/yanqing/'+(f'index_{page+1}.html' if page else '')
        return parse_list(self.http.get(url))
    def metadata(self, entry):
        meta=parse_metadata(self.http.get(entry.url),entry)
        front=parse_frontmatter(self.http.get(entry.url[:-5]+'_2.html'))
        if not front: raise ValueError('52简介章节边界未识别')
        return replace(meta,description=front)
    def download(self,url): return 'queued', self.http.safe(url)
