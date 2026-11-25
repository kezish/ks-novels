import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"server"))
from sources import wx256 as w
import sourcekit_bridge as b
import search_details as d
from fastapi import HTTPException

URL=w.BASE+"/e/search/result/?searchid=123"
PAGE2=w.BASE+"/e/search/result/index.php?page=1&searchid=123"
def page(id=1,more=True,keyword="重生"):
    return f'<article class="main_content"><div class="main_left"><div class="position">100项符合"{keyword}"的查询结果</div><header><h3><a href="/read/{id}">重生{ id } 作者：合成作者</a></h3><section>短简介</section><time>2026-10-28</time></header><div class="page">'+('<a href="/e/search/result/index.php?page=1&amp;searchid=123">下一页</a>' if more else '')+'</div></div></article>'
class WXTests(unittest.TestCase):
    def test_parse_and_redirect_guard(self):
        rows,nxt=w.parse_results(page(),URL,"重生")
        self.assertEqual(nxt,PAGE2)
        self.assertEqual(rows[0]["author"],"合成作者")
        self.assertEqual(rows[0]["book_url"],w.BASE+"/read/1/")
        self.assertEqual(rows[0]["published"],"2026-10-28")
        self.assertNotIn("download_unavailable",rows[0])
        with self.assertRaises(ValueError):w.parse_results('<h2>首页推荐</h2>',URL,"重生")
        with self.assertRaises(ValueError):w.parse_results(page(),URL,"其他")
        with self.assertRaises(ValueError):w.parse_results(page().replace('searchid=123','searchid=456'),URL,"其他")
        for u in ("https://evil.example/read/1/","https://www.256wx.org/read/1/1.html","https://www.256wx.org/read/1/?x=2"):
            with self.assertRaises(ValueError):w.book_url(u)
    def test_session_pagination_cooldown(self):
        x=w.WXSearch()
        with patch.object(x,"request",side_effect=[(page(),URL),(page(2,False),PAGE2)]) as req:
            a,st=x.search("重生")
            self.assertEqual(st["next_page"],2)
            self.assertIn(b"keyboard=",req.call_args.args[1])
            c,st2=x.search("重生",2)
            self.assertEqual(st2["state"],"completed")
            self.assertIsNone(st2["next_page"])
            self.assertNotEqual(a[0]["book_url"],c[0]["book_url"])
            self.assertEqual(req.call_args.args,(PAGE2,))
            self.assertEqual(x.search("重生")[0],a)
            self.assertEqual(x.search("其他")[1]["state"],"throttled")
            self.assertEqual(req.call_count,2)
    def test_invalid_result_and_intro_boundary(self):
        intro='<div id="nr1" class="book_con fix"><p>书名</p><p>文案</p><p>内容标签：百合</p><p>第1章 见面</p><p>正文</p></div>'
        self.assertEqual(w.parse_intro(intro),"书名\n\n文案\n\n内容标签：百合")
        self.assertEqual(w.parse_intro('<div id="nr1" class="book_con"><p>无法确认边界</p></div>'),"")
        with patch.object(w.HTTP,"get",return_value=intro):
            self.assertEqual(w.read_intro(w.BASE+"/read/1/")["kind"],"full")
        with patch.object(w.HTTP,"get",side_effect=["<html></html>",'<div id="nr_history">摘要</div>']):
            self.assertEqual(w.read_intro(w.BASE+"/read/1/"),{"description":"摘要","kind":"excerpt"})
        self.assertEqual(d.identity(w.SITE,w.BASE+"/read/1/")[1],"1")
        with self.assertRaises(HTTPException):d.identity(w.SITE,w.BASE+"/read/1/1.html")
    def test_bridge(self):
        rows,_=w.parse_results(page(),URL,"重生")
        st={"state":"completed","count":1,"next_page":2}
        with patch.object(b,"BIN","/fake"),patch.object(w.SEARCHER,"search",return_value=(rows,st)) as m:
            r=asyncio.run(b.search("重生",[w.SITE],order="newest"))
            self.assertEqual(r["results"],rows)
            self.assertEqual(r["statuses"][w.SITE]["next_page"],2)
            m.assert_called_once_with("重生",1)
            r=asyncio.run(b.search("重生",[w.SITE],category="gl"))
            self.assertEqual(r["statuses"][w.SITE]["state"],"unsupported")
