import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"server"))
import search_details as d
from fastapi import HTTPException

class DetailsTests(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(d.identity("sk_shuku52","https://52shuku.net/gl/a.html")[1],"/gl/a.html")
        for site,url in [("sk_czbooks","https://evil.example/n/a"),("sk_shuku52","https://www.52shuku.net/gl/a_2.html"),("sk_jiujiu","https://www.ijjxsxzw.com/txt/123.html?q=1"),("sk_biquge5","https://czbooks.net/n/a")]:
            with self.assertRaises(HTTPException):d.identity(site,url)
    def test_catalog_exact_source(self):
        from catalog.store import init,connect,upsert
        from sources.base import Entry,Metadata
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"catalog.sqlite3";init(path)
            e=Entry("123","https://www.ijjxsxzw.com/txt/123.html","合成小说","作者","言情","2026-11-24","latest_upload")
            with connect(path) as db:upsert(db,"jiujiu",e,Metadata(e.title,e.author,e.category,"完整简介",e.uploaded),"2026-11-24")
            with patch.object(d,"read_engine",side_effect=AssertionError("must not fetch")):
                r=asyncio.run(d.detail("sk_jiujiu",e.url,path))
            self.assertEqual(r["description"],"完整简介")
            self.assertEqual(r["kind"],"catalog")
    def test_52_frontmatter(self):
        with patch("sources.base.HTTP.get",return_value='<div id="text"><p>完整简介</p><p>第1章</p><p>正文不应展示</p></div>'):
            result=d.read52("https://www.52shuku.net/gl/a.html")
        self.assertEqual(result["description"],"完整简介")
        self.assertEqual(result["kind"],"full")
    def test_engine_cache(self):
        async def run(path):
            d.CACHE.clear();d.LAST.clear()
            with patch.object(d.bridge,"BIN","/fake"),patch.object(d,"read_engine",return_value={"description":"合成简介","kind":"site"}) as fetch:
                for i in range(2):
                    self.assertEqual((await d.detail("sk_czbooks","https://czbooks.net/n/cachetest",path))["description"],"合成简介")
                self.assertEqual(fetch.call_count,1)
        with tempfile.TemporaryDirectory() as tmp:asyncio.run(run(Path(tmp)/"absent.db"))
