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
    def test_append_active_editions(self):
        from catalog.store import append_active
        from catalog.cli import run
        from unittest.mock import patch
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz=ZoneInfo('Asia/Singapore')
        day1=datetime(2026,11,25,8,0,tzinfo=tz)
        day2=datetime(2026,11,26,8,0,tzinfo=tz)
        def add(n,stamp):
            e=Entry(str(n),'https://example/'+str(n),str(n),'作者','仙侠',stamp.date().isoformat(),'latest_upload')
            with connect(self.path) as db:
                return upsert(db,'jiujiu',e,Metadata(e.title,e.author,e.category,'简介',e.uploaded),stamp.isoformat())
        add(1,day1)
        with patch('catalog.store.now',return_value=day1):
            self.assertEqual(append_active(self.path),0)
            self.assertTrue(publish(self.path))
        add(2,day1+timedelta(hours=1))
        with patch('catalog.store.now',return_value=day1+timedelta(hours=2)), patch('catalog.cli.sync',return_value=[]):
            self.assertEqual(run('sync',self.path),0)
            self.assertEqual(append_active(self.path),0)
        # Until 08:00, the preceding edition remains active.
        add(3,day2-timedelta(hours=1))
        with patch('catalog.store.now',return_value=day2-timedelta(minutes=30)):
            self.assertEqual(append_active(self.path),1)
        # A daily run creates the new edition instead of appending to yesterday.
        add(4,day2)
        with patch('catalog.store.now',return_value=day2), patch('catalog.cli.sync',return_value=[]):
            self.assertEqual(run('daily',self.path),0)
        add(5,day2+timedelta(hours=1))
        with patch('catalog.store.now',return_value=day2+timedelta(hours=2)):
            self.assertEqual(append_active(self.path),1)
            self.assertEqual(append_active(self.path),0)
        with connect(self.path) as db:
            self.assertEqual({b['title'] for b in books_for(db,'2026-11-25')},{'1','2','3'})
            self.assertEqual({b['title'] for b in books_for(db,'2026-11-26')},{'4','5'})
            self.assertEqual(db.execute('SELECT count(*) FROM notification_outbox').fetchone()[0],2)

    def test_live_progress_and_interruption(self):
        import fcntl
        from catalog.progress import Progress,status
        class Adapter:
            modes=('latest_upload',)
            display_name='测试书源'
        with (self.path.parent/'catalog-sync.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            legacy=status(self.path)
            self.assertEqual(legacy['state'],'running')
            self.assertTrue(legacy['legacy'])
            p=Progress(self.path,{'test':Adapter()})
            stage=p.start(0,'2026-11-17')
            stage.update(page=2,page_total=10,page_done=4,processed=14)
            p.write()
            active=status(self.path)
            self.assertEqual(active['state'],'running')
            self.assertEqual(active['stages'][0]['page_done'],4)
            self.assertTrue(active['lock_busy'])
        self.assertEqual(status(self.path)['state'],'interrupted')
        p.finish(stage,True,'')
        p.end()
        self.assertEqual(status(self.path)['state'],'done')

    def test_publication_order_and_last_sync(self):
        from catalog.progress import Progress,status
        from unittest.mock import patch
        class Adapter:
            modes=("latest_upload",)
            display_name="测试"
        p=Progress(self.path,{"jiujiu":Adapter()})
        with connect(self.path) as db:
            for source,id,title,uploaded in [
                ("jiujiu","1","新书","2026-11-25 10:30:00"),
                ("jiujiu","2","旧书","2026-11-24"),
                ("52shuku","3","旧书","2026-11-25 09:30:00"),
                ("jiujiu","4","最新","2026-11-25 12:00:00")]:
                e=Entry(id,"https://example/"+id,title,"作者","言情",uploaded,"latest_upload")
                upsert(db,source,e,Metadata(e.title,e.author,e.category,"简介",uploaded),now().isoformat())
            items=books_for(db,now().date().isoformat(),preview=True)
            self.assertEqual([b["title"] for b in items],["最新","新书","旧书"])
            self.assertEqual(items[-1]["uploaded"],"2026-11-25 09:30:00")
        p.finish(p.start(0,"2026-11-17"),True,"")
        p.end()
        with patch("catalog.progress.next_sync",return_value="2026-11-25T10:15:00+00:00"):
            result=status(self.path)
            self.assertEqual(result["last_sync"]["new_books"],3)
            self.assertEqual(result["last_sync"]["new_sources"],4)
            self.assertEqual(result["next_sync"],"2026-11-25T10:15:00+00:00")
            saved=result["last_sync"]
            Progress(self.path,{"jiujiu":Adapter()})
            self.assertEqual(status(self.path)["last_sync"],saved)
        publish(self.path)
        with connect(self.path) as db:
            self.assertEqual([b["title"] for b in books_for(db,now().date().isoformat())],["最新","新书","旧书"])

    def test_next_sync_actual_timer(self):
        from catalog.progress import next_sync
        from unittest.mock import patch
        from subprocess import CompletedProcess
        with patch.dict("os.environ",{"NOVELS_CATALOG_EXTERNAL_TIMER":"1"}),patch("catalog.progress.subprocess.run",side_effect=[
            CompletedProcess([],0,"ActiveState=active\\nNextElapseUSecRealtime=Wed 2026-11-25 10:15:00 UTC\\n".replace("\\n","\n"),""),
            CompletedProcess([],0,"ActiveState=active\\nNextElapseUSecRealtime=Thu 2026-11-26 00:00:00 UTC\\n".replace("\\n","\n"),"")]):
            self.assertEqual(next_sync(),"2026-11-25T10:15:00+00:00")

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
        from catalog.progress import status
        progress=status(self.path)
        self.assertEqual(progress['state'],'failed')
        self.assertEqual(progress['stages'][0]['errors'],1)
        self.assertEqual(progress['stages'][0]['added_sources'],1)
        self.assertEqual(progress['stages'][0]['page_done'],2)
        self.assertFalse(result[0][2])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sync_state').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM books').fetchone()[0],1)

if __name__=='__main__':unittest.main()
