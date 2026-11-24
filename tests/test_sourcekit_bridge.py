import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"server"))
import sourcekit_bridge as b

class BridgeTests(unittest.TestCase):
    def test_urls(self):
        with patch.object(b,"BIN","/fake"):
            self.assertEqual(b.normalize_external("https://czbooks.net/n/abc#top"),"https://czbooks.net/n/abc")
            self.assertEqual(b.normalize_external("https://www.biquge5.com/123/"),"https://www.biquge5.com/123/")
            for url in ("https://evil.example/n/abc","https://czbooks.net/n/abc/def","https://czbooks.net@evil.example/n/abc","https://www.biquge5.com/123/4.html"):
                with self.assertRaises(ValueError):b.normalize_external(url)
    def test_commands(self):
        with patch.object(b,"BIN","/fake"),patch.object(b,"USE_52",False):
            self.assertIsNone(b.command("https://www.52shuku.net/gl/test.html",Path("/out"),Path("/data")))
            self.assertEqual(b.command("https://czbooks.net/n/abc",Path("/out"),Path("/data"))[:2],["/fake","download"])
    def test_search(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"fake"
            p.write_text('#!/bin/sh\nprintf \'%s\' \'{"results":[{"site":"sk_biquge5","title":"test","author":"author","book_url":"https://www.biquge5.com/123/"},{"site":"sk_biquge5","book_url":"https://evil.example/"}],"statuses":{"sk_biquge5":{"state":"completed","count":1},"sk_czbooks":{"state":"blocked"}}}\'\n')
            p.chmod(0o700)
            with patch.object(b,"BIN",str(p)):
                result=asyncio.run(b.search("test",["sk_biquge5","sk_czbooks"]))
                self.assertEqual(len(result["results"]),1)
                self.assertEqual(result["statuses"]["sk_czbooks"]["state"],"blocked")
