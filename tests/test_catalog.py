import sys
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from catalog.store import connect, init, upsert, publish, books_for, now, normalized
from catalog.sync import sync
from sources.base import Entry,Metadata
from sources.jiujiu import parse_list as jlist,parse_metadata as jmeta,parse_download
from sources.shuku52 import parse_list as slist,parse_metadata as smeta,parse_frontmatter

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'catalog.db'
        init(self.path)
    def tearDown(self): self.tmp.cleanup()
    def test_jiujiu(self):
        entries,more=jlist('<div class="listbg"><span class="title"><a href="/txt/123.html">书名</a></span><span class="oldDate">2026-11-24 21:59:31</span><a href="/zuozhe/5.html">作者</a></div><a>下一页</a>','new_book')
        self.assertTrue(more)
        self.assertEqual(entries[0].author,'作者')
        m=jmeta('<div class="kv"><p>书籍分类：仙侠</p><p>上传时间：2026-11-24</p></div><div class="desc"><style>abc</style>第一段<br>第二段</div>',entries[0])
        self.assertEqual(m.description,'第一段\n第二段')
        self.assertEqual(m.uploaded,'2026-11-24 21:59:31')
        self.assertEqual(m.category,'仙侠')
        url=parse_download('<a class="download_1" href="/e/DownSys/doaction.php?enews=DownSoft&amp;id=123&amp;pathid=0&amp;pass=abc">TXT</a>','123')
        self.assertIn('pass=abc',url)
        with self.assertRaises(ValueError): parse_download('<a class="download_1" href="https://evil.example/evil">TXT</a>','123')
    def test_52(self):
        entries,_=slist('<article class="excerpt"><h2><a href="https://www.52shuku.net/yanqing/24_b/xyz.html">书名_作者【完结+番外】</a></h2><span class="note">[无ＣＰ向] 简介</span><span class="muted">(2026-11-24)</span></article>')
        e=entries[0]
        self.assertEqual((e.title,e.author,e.category),('书名','作者','无ＣＰ向'))
        m=smeta('<h1 class="article-title">书名_作者【完结】</h1><time>2026年11月24日 15:00:00</time><article class="article-content"><p>小说简介：</p><p>简介第一段</p><p>所属专题：</p></article>',e)
        self.assertEqual(m.description,'简介第一段')
        front=parse_frontmatter('<article class="article-content"><div id="text"><p>简介第一段</p><p>简介第二段</p><p>第 1章 正文</p><p>正文不能入简介</p></div></article>')
        self.assertEqual(front,'简介第一段\n\n简介第二段')
        self.assertEqual(parse_frontmatter('<div id="text"><p>无章节边界</p></div>'),'')
    def test_dedup_editions(self):
        e=Entry('1','https://example/a','《Ａ书》','作者','仙侠','2026-11-24','latest_upload')
        m=Metadata(e.title,e.author,e.category,'简介',e.uploaded)
        with connect(self.path) as db:
            b=upsert(db,'jiujiu',e,m,now().isoformat())
            e2=Entry('2','https://example/b','A书','作者','仙侠','2026-11-24','latest_upload')
            m2=Metadata(e2.title,e2.author,e2.category,'更完整的简介',e2.uploaded)
            self.assertEqual(b,upsert(db,'52shuku',e2,m2,now().isoformat()))
            e3=Entry('3','https://example/c','A书','另一作者','仙侠','2026-11-24','new_book')
            self.assertNotEqual(b,upsert(db,'jiujiu',e3,Metadata(e3.title,e3.author,e3.category,'简介',e3.uploaded),now().isoformat()))
        today=now().date().isoformat()
        self.assertTrue(publish(self.path,today))
        self.assertFalse(publish(self.path,today))
        with connect(self.path) as db:
            items=books_for(db,today)
            self.assertEqual(len(items),2)
            self.assertEqual(len(next(x for x in items if x['id']==b)['sources']),2)
            self.assertEqual(db.execute('SELECT count(*) FROM notification_outbox').fetchone()[0],1)
        tomorrow=(now().date()+timedelta(days=1)).isoformat()
        publish(self.path,tomorrow)
        with connect(self.path) as db: self.assertEqual(books_for(db,tomorrow),[])
    def test_fallback_schedule(self):
        from catalog.scheduler import next_action
        stamp=now().replace(hour=7,minute=0,second=0,microsecond=0)
        self.assertEqual(next_action(self.path,stamp),'sync')
        with connect(self.path) as db:
            db.execute("INSERT INTO sync_runs(source,mode,started,ok,error) VALUES(?,?,?,?,?)",('jiujiu','latest_upload',stamp.isoformat(),1,''))
        self.assertIsNone(next_action(self.path,stamp))
        self.assertEqual(next_action(self.path,stamp.replace(hour=8)),'daily')
        publish(self.path,stamp.date().isoformat())
        self.assertEqual(next_action(self.path,stamp.replace(hour=8)),'sync')
    def test_sync_failure_no_watermark(self):
        stamp=now().date().isoformat()
        class Adapter:
            modes=('latest_upload',)
            def page(self,mode,page):
                return [Entry('1','https://example/a','书','作者','仙侠',stamp,mode),
                        Entry('2','https://example/b','书2','作者','仙侠',stamp,mode)],False
            def metadata(self,e):
                if e.external_id=='1': raise ValueError('故障')
                return Metadata(e.title,e.author,e.category,'简介',e.uploaded)
        result=sync(self.path,{'test':Adapter()})
        self.assertFalse(result[0][2])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sync_state').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM books').fetchone()[0],1)

if __name__=='__main__':unittest.main()
