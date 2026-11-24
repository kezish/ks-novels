import secrets
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"server"))
from session_auth import SessionStore,BrowserAuth,safe_next

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"session.db"
        self.now=1000
        self.fixture=secrets.token_urlsafe(32)
    def tearDown(self): self.tmp.cleanup()
    def store(self, fixture=None):
        return SessionStore(self.db,fixture or self.fixture,absolute=1000,idle=100,
                            limit=3,window=60,clock=lambda:self.now)
    def test_persistence_and_rotation(self):
        s=self.store()
        token,_=s.login("ks",self.fixture,"ip")
        self.assertTrue(s.valid(token))
        with sqlite3.connect(self.db) as db:
            self.assertNotEqual(db.execute("select token_hash from sessions").fetchone()[0],token)
        self.assertTrue(self.store().valid(token))
        self.assertFalse(self.store(secrets.token_urlsafe(32)).valid(token))
        self.assertFalse(self.store().valid(token))
    def test_idle_and_absolute(self):
        s=self.store()
        token,_=s.login("ks",self.fixture,"ip")
        self.now+=99
        self.assertTrue(s.valid(token))
        self.now+=101
        self.assertFalse(s.valid(token))
        self.now=2000
        token,_=s.login("ks",self.fixture,"ip")
        for _ in range(11):
            self.now+=90
            self.assertTrue(s.valid(token))
        self.now+=10
        self.assertFalse(s.valid(token))
    def test_rate_limit_persists(self):
        s=self.store()
        for _ in range(3):
            self.assertEqual(s.login("ks","wrong","ip"),(None,0))
        self.assertGreater(self.store().login("ks",self.fixture,"ip")[1],0)
        self.assertTrue(s.login("ks",self.fixture,"other")[0])
        self.now+=61
        self.assertTrue(self.store().login("ks",self.fixture,"ip")[0])
    def test_revoke_and_next(self):
        s=self.store()
        t,_=s.login("ks",self.fixture,"ip")
        t2,_=s.login("ks",self.fixture,"ip",t)
        self.assertFalse(s.valid(t))
        self.assertTrue(s.valid(t2))
        s.revoke(t2)
        self.assertFalse(s.valid(t2))
        self.assertEqual(safe_next("//evil.example"),"/daily")
        self.assertEqual(safe_next("/daily/2026-11-24"),"/daily/2026-11-24")
    def test_secure_cookie_name(self):
        auth=BrowserAuth(Path(self.tmp.name),Path("."),"https://novels.example")
        self.assertTrue(auth.secure)
        self.assertEqual(auth.cookie,"__Host-novels_session")
