"""Source contract and restricted HTTP/HTML utilities (stdlib only)."""
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import re
import time

@dataclass(frozen=True)
class Entry:
    external_id: str
    url: str
    title: str
    author: str
    category: str
    uploaded: str
    mode: str

@dataclass(frozen=True)
class Metadata:
    title: str
    author: str
    category: str
    description: str
    uploaded: str

class SourceAdapter(Protocol):
    source_id: str
    display_name: str
    download_kind: str
    modes: tuple[str, ...]
    def page(self, mode: str, page: int) -> tuple[list[Entry], bool]: ...
    def metadata(self, entry: Entry) -> Metadata: ...
    def download(self, url: str) -> tuple[str, str]: ...

class Node:
    def __init__(self, tag='', attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
    def has(self, cls):
        return cls in (self.attrs.get('class') or '').split()
    def find(self, tag=None, cls=None):
        return [n for n in self.walk() if (tag is None or n.tag == tag) and (cls is None or n.has(cls))]
    def walk(self):
        for n in self.children:
            if isinstance(n, Node):
                yield n
                yield from n.walk()
    def text(self):
        if self.tag in ('script', 'style', 'noscript'):
            return ''
        if self.tag == 'br':
            return '\n'
        s = ''.join(n.text() if isinstance(n, Node) else n for n in self.children)
        return s + ('\n' if self.tag in ('p','div','li') else '')
class Parser(HTMLParser):
    void = set('area base br col embed hr img input link meta param source track wbr'.split())
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in self.void:
            self.stack.append(n)
    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, data):
        self.stack[-1].children.append(data)

def parse(html): return Parser(html).root
def first(n, tag=None, cls=None):
    return next(iter(n.find(tag, cls)), Node())
def clean(text):
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())
def day(value):
    m = re.search(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})', value)
    if not m:
        raise ValueError('Missing upload date')
    return date(*map(int, m.groups())).isoformat()
def date_time(value):
    d = day(value)
    m = re.search(r'\d{2}:\d{2}:\d{2}', value)
    return d + (' '+m.group() if m else '')

class HTTP:
    def __init__(self, hosts, interval=3.0):
        self.hosts = set(hosts)
        self.interval = interval
        self.last = 0
        parent = self
        class Redirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                parent.safe(newurl)
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        self.opener = build_opener(Redirect())
    def safe(self, url):
        u = urlsplit(url)
        if u.scheme != 'https' or u.hostname not in self.hosts or u.port not in (None,443) or u.username or u.password:
            raise ValueError('Unexpected source URL')
        return url
    def get(self, url):
        self.safe(url)
        time.sleep(max(0, self.interval-(time.monotonic()-self.last)))
        self.last = time.monotonic()
        with self.opener.open(Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; personal novel catalog)', 'Accept':'text/html'}), timeout=20) as r:
            self.safe(r.url)
            raw = r.read(3_000_001)
            if len(raw)>3_000_000:
                raise ValueError('HTML exceeds limit')
            return raw.decode('utf-8', errors='replace')
