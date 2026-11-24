import re
from urllib.parse import urljoin, urlsplit, parse_qs
from .base import Entry, Metadata, HTTP, parse, first, clean, date_time

BASE = 'https://www.ijjxsxzw.com'
MODES = {'latest_upload':1, 'new_book':5}

def list_url(mode, page):
    return BASE + f'/txt/toplist-p_{page}-c_1-n_0-l_10-t_6-p_1-s_0-q_0-r_{MODES[mode]}-m_0.html'

def parse_list(html, mode):
    root = parse(html)
    entries = []
    for card in root.find(cls='listbg'):
        link = first(first(card, cls='title'), 'a')
        href = link.attrs.get('href','')
        m = re.fullmatch(r'/txt/(\d+)\.html', href)
        if not m:
            continue
        author = next((a.text().strip() for a in card.find('a') if a.attrs.get('href','').startswith('/zuozhe/')), '')
        entries.append(Entry(m[1], BASE+href, link.text().strip(), author, '', date_time(first(card, cls='oldDate').text()), mode))
    if not entries:
        raise ValueError('久久榜单为空或页面结构异常')
    return entries, any(a.text().strip() == '下一页' for a in root.find('a'))

def parse_metadata(html, entry):
    root = parse(html)
    detail = first(root, cls='kv')
    fields = {}
    for p in detail.find('p'):
        k, _, v = p.text().partition('：')
        fields[k.strip()] = v.strip()
    desc = clean(first(root, cls='desc').text())
    if not desc:
        raise ValueError('久久简介未找到')
    return Metadata(entry.title, fields.get('书籍作者') or entry.author, fields.get('书籍分类',''), desc, max(date_time(fields.get('上传时间') or entry.uploaded),entry.uploaded))

def parse_download(html, external_id):
    root = parse(html)
    href = first(root,'a','download_1').attrs.get('href','')
    url = urljoin(BASE,href)
    u = urlsplit(url)
    q = parse_qs(u.query)
    if (u.scheme != 'https' or u.hostname != 'www.ijjxsxzw.com' or u.port not in (None,443) or u.username or u.password or u.path != '/e/DownSys/doaction.php'
        or q.get('id') != [external_id] or q.get('pathid') != ['0'] or q.get('enews') != ['DownSoft']):
        raise ValueError('原站TXT下载地址异常')
    return url

class Jiujiu:
    source_id = 'jiujiu'
    display_name = '久久小说'
    download_kind = 'direct'
    modes = tuple(MODES)
    def __init__(self):
        self.http = HTTP({'www.ijjxsxzw.com','m.ijjxsxzw.com'})
    def page(self, mode, page):
        return parse_list(self.http.get(list_url(mode,page)),mode)
    def metadata(self, entry):
        return parse_metadata(self.http.get(entry.url),entry)
    def download(self, url):
        m = re.fullmatch(re.escape(BASE)+r'/txt/(\d+)\.html',url)
        if not m: raise ValueError('Invalid book URL')
        return 'direct', parse_download(self.http.get(BASE+'/txt/dl-'+m[1]+'.html'),m[1])
