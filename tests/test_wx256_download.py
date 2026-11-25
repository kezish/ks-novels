import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"server"))
from sources import wx256_download as d
import sourcekit_bridge as b

BOOK="https://www.256wx.org/read/123/"
def directory(numbers=(1,2)):
    return '<h1 id="nr_title">合成小说</h1><ul id="nr1">'+''.join(f'<li><a href="/read/123/{i}.html">第{i}页</a></li>' for i in numbers)+'</ul>'
def reader_page(n,total=2,content=None):
    nav='<ul><li><a href="/read/123/">目录</a></li>'
    if n>1:nav+=f'<li><a href="/read/123/{n-1}.html">上一页</a></li>'
    if n<total:nav+=f'<li><a href="/read/123/{n+1}.html">下一页</a></li><li><a href="/read/123/{total}.html">尾页</a></li>'
    nav+='</ul>'
    content=content or ("<p>完整文案</p><p>内容标签：合成</p><p>第1章</p><p>正文一<br>下一行</p>" if n==1 else "<p>正文二</p>")
    return f'<h1 id="nr_title">合成小说({n})</h1><div class="book_con fix" id="nr1">{content}{nav}</div><div>无关推荐</div>'
class FakeReader:
    def __init__(self, pages):
        self.pages=pages
        self.requests=[]
    def get(self,url):
        self.requests.append(url)
        return self.pages[url]
class WXDownloadTests(unittest.TestCase):
    def test_complete_export(self):
        r=FakeReader({BOOK:directory(),BOOK+"1.html":reader_page(1),BOOK+"2.html":reader_page(2)})
        progress=[]
        with tempfile.TemporaryDirectory() as tmp:
            f=d.export(BOOK,tmp,r,lambda *x:progress.append(x))
            self.assertEqual(f.parent,Path(tmp))
            self.assertEqual(f.read_text(),"完整文案\n\n内容标签：合成\n\n第1章\n\n正文一\n下一行\n\n正文二\n")
            self.assertEqual(progress,[(0,2,0),(1,2,0),(2,2,0)])
            self.assertEqual(r.requests,[BOOK,BOOK+"1.html",BOOK+"2.html"])
    def test_bonus_suffix_title(self):
        for suffix in ("+番外","＋番外"):
            title="合成小说"+suffix
            for n in (1,2):
                raw=reader_page(n).replace(f"合成小说({n})",f"合成小说({n})"+suffix)
                self.assertTrue(d.read_page(raw,BOOK,title,n,2))
                for invalid in (raw.replace(f"({n})",f"({n+1})"),raw.replace("合成小说","其他小说"),raw.replace(suffix,""),raw.replace(suffix,"+其他")):
                    with self.assertRaises(ValueError):d.read_page(invalid,BOOK,title,n,2)
                for good in (raw.replace(f"({n})",f"（{n}）"),raw.replace(f"({n})"+suffix,suffix+f"({n})")):
                    self.assertTrue(d.read_page(good,BOOK,title,n,2))
        r=FakeReader({BOOK:directory().replace("合成小说","合成小说+番外"),BOOK+"1.html":reader_page(1).replace("合成小说(1)","合成小说(1)+番外"),BOOK+"2.html":reader_page(2).replace("合成小说(2)","合成小说(2)+番外")})
        with tempfile.TemporaryDirectory() as tmp:
            f=d.export(BOOK,tmp,r)
            self.assertTrue(f.name.startswith("合成小说+番外"))
            self.assertIn("完整文案",f.read_text())
    def test_invalid_directory(self):
        for numbers in ((1,3),(1,1),(2,1),()):
            with self.assertRaises(ValueError):d.read_directory(directory(numbers),BOOK)
        with self.assertRaises(ValueError):d.read_directory(directory().replace("/read/123/2.html","/read/456/2.html"),BOOK)
    def test_missing_or_repeated_content_no_output(self):
        for second in (reader_page(1),reader_page(2,content="<p> </p>"),reader_page(2,content="<p>正文一</p>"),reader_page(2).replace('上一页','错误'),reader_page(2,total=3)):
            r=FakeReader({BOOK:directory(),BOOK+"1.html":reader_page(1,content="<p>正文一</p>"),BOOK+"2.html":second})
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):d.export(BOOK,tmp,r)
                self.assertEqual(list(Path(tmp).iterdir()),[])
    def test_queue_routing(self):
        with patch.object(b,"BIN","/fake"):
            self.assertEqual(b.normalize_external(BOOK.rstrip("/")),BOOK)
            args=b.command(BOOK,Path("/out"),Path("/data"))
            self.assertTrue(args[1].endswith("wx256_download_cli.py"))
            self.assertEqual(args[2:],[BOOK,"--output-dir","/out"])
            for url in (BOOK+"1.html","https://www.256wx.org/txt/123.html","https://www.256wx.org/read/123/?x=1"):
                with self.assertRaises(ValueError):b.normalize_external(url)
