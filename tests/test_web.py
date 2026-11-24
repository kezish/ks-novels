"""Smoke and security regressions for the standalone ks-novels app."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlencode
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class WebIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ks-novels-test-")
        cls.root = Path(cls.temp.name)
        cls.passwd = cls.root / "password"
        cls.passwd.write_text("local-smoke-password-long-enough-for-integration\n")
        cls.scraper = cls.root / "fake-scraper"
        cls.scraper.write_text("""#!/bin/sh
out=''
prev=''
for arg in "$@"; do
  if [ "$prev" = '--output-dir' ]; then out="$arg"; fi
  prev="$arg"
done
test -n "$out" || exit 22
mkdir -p "$out"
printf 'downloading: 1/2 completed, 0 failed\\n' >&2
sleep 0.12
printf 'completed: 2/2 completed, 0 failed\\n' >&2
printf '【小说简介】\\n保留小剧场。\\n\\n第1章\\n正文。\\n' > "$out/测试小说-0123456789abcdef.txt"
echo "$out/测试小说-0123456789abcdef.txt"
""")
        cls.scraper.chmod(0o700)
        cls.sourcekit = cls.root / "fake-sourcekit"
        cls.sourcekit.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = search ]; then\n"
            "printf '%s\\n' '{\"results\":[{\"site\":\"sk_czbooks\",\"title\":\"測試\",\"author\":\"作者\",\"book_url\":\"https://czbooks.net/n/test1\"}],\"statuses\":{\"sk_czbooks\":{\"state\":\"completed\",\"count\":1}}}'\n"
            "else\n"
            "exec " + str(cls.scraper) + " \"$@\"\n"
            "fi\n"
        )
        cls.sourcekit.chmod(0o700)
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = os.environ.copy()
        env.update(
            NOVELS_DATA_DIR=str(cls.root / "data"),
            NOVELS_PASSWORD_FILE=str(cls.passwd),
            NOVELS_YURI_BIN=str(cls.scraper),
            NOVELS_SOURCEKIT_BIN=str(cls.sourcekit),
            NOVELS_SOURCEKIT_52="0",
            NOVELS_ORIGIN=cls.base,
        )
        cls.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "app:app", "--app-dir", str(ROOT / "server"),
             "--host", "127.0.0.1", "--port", str(cls.port), "--workers", "1"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        for _ in range(60):
            if cls.proc.poll() is not None:
                raise RuntimeError(f"uvicorn exited with code {cls.proc.returncode}")
            try:
                status, _, _ = cls.req("/healthz", authenticated=False)
                if status == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("uvicorn did not start")
        code,headers,_=cls.login()
        if code!=303: raise RuntimeError("Test login failed")
        cls.cookie=headers["Set-Cookie"].split(";",1)[0]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=10)
        cls.temp.cleanup()

    @classmethod
    def req(cls, path, method="GET", data=None, authenticated=True, origin=None):
        headers = {}
        if authenticated:
            headers["Cookie"] = cls.cookie
        if data is not None:
            headers["Content-Type"] = "application/json"
        if origin is not None:
            headers["Origin"] = origin
        request = urllib.request.Request(
            cls.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with cls.http.open(request, timeout=12) as result:
                return result.status, result.headers, result.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    @classmethod
    def login(cls, next_page="/daily", origin=None):
        request=urllib.request.Request(cls.base+"/login",
            data=urlencode({"username":"ks","password":cls.passwd.read_text().strip(),"next":next_page}).encode(),
            headers={"Content-Type":"application/x-www-form-urlencoded","Origin":origin or cls.base},
            method="POST")
        try:
            with cls.http.open(request,timeout=12) as r: return r.status,r.headers,r.read()
        except urllib.error.HTTPError as r: return r.code,r.headers,r.read()

    def test_persistent_login_and_logout(self):
        code,headers,_=self.req("/daily/2026-11-24",authenticated=False)
        self.assertEqual(code,303)
        self.assertIn("next=%2Fdaily%2F2026-11-24",headers["Location"])
        self.assertEqual(self.req("/login",authenticated=False)[0],200)
        self.assertEqual(self.login(origin="https://attacker.invalid")[0],403)
        code,headers,_=self.login(next_page="//attacker.invalid")
        self.assertEqual(code,303)
        self.assertEqual(headers["Location"],"/daily")
        cookie=headers["Set-Cookie"]
        self.assertIn("HttpOnly",cookie)
        self.assertIn("SameSite=lax",cookie)
        self.assertIn("Max-Age=15552000",cookie)
        old=self.__class__.cookie
        self.__class__.cookie=cookie.split(";",1)[0]
        try:
            self.assertEqual(self.req("/api/daily")[0],200)
            self.assertEqual(self.req("/logout",method="POST",origin="https://attacker.invalid")[0],403)
            self.assertEqual(self.req("/logout",method="POST",origin=self.base)[0],303)
            self.assertEqual(self.req("/api/daily")[0],401)
        finally:
            self.__class__.cookie=old
        self.assertEqual(self.req("/api/daily")[0],200)

    def test_auth_and_static_assets(self):
        self.assertEqual(self.req("/healthz", authenticated=False)[0], 200)
        self.assertEqual(self.req("/api/batches", authenticated=False)[0], 401)
        self.assertEqual(self.req("/api/catalog/sync-status", authenticated=False)[0],401)
        code,_,body=self.req("/api/catalog/sync-status")
        self.assertEqual(code,200)
        self.assertIn("books",json.loads(body))
        self.assertEqual(self.req("/")[0], 200)
        status, _, html = self.req("/")
        self.assertIn("viewport", html.decode())
        self.assertIn("下载 TXT", html.decode())
        self.assertEqual(self.req("/assets/app.js")[0], 200)
        self.assertEqual(self.req("/assets/style.css")[0], 200)

    def test_sourcekit_search_and_auth(self):
        self.assertEqual(self.req("/api/sourcekit/sources", authenticated=False)[0],401)
        status,_,body=self.req("/api/sourcekit/sources")
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)["enabled"])
        query={"keyword":"測試","sites":["sk_czbooks"]}
        self.assertEqual(self.req("/api/sourcekit/search",method="POST",data=query,origin="https://attacker.invalid")[0],403)
        status,_,body=self.req("/api/sourcekit/search",method="POST",data=query,origin=self.base)
        self.assertEqual(status,200,body)
        result=json.loads(body)
        self.assertEqual(result["results"][0]["book_url"],"https://czbooks.net/n/test1")
        self.assertEqual(result["statuses"]["sk_czbooks"]["count"],1)
        self.assertEqual(self.req("/api/sourcekit/search",method="POST",data={"keyword":"x","sites":["invalid"]},origin=self.base)[0],422)

    def test_url_validation_and_origin(self):
        self.assertEqual(
            self.req("/api/batches", method="POST",
                     data={"urls": ["https://127.0.0.1/secret.html"]},
                     origin=self.base)[0],
            422,
        )
        self.assertEqual(
            self.req("/api/batches", method="POST",
                     data={"urls": ["https://www.52shuku.net/gl/h1R2.html"]},
                     origin="https://attacker.invalid")[0],
            403,
        )

    def test_job_queue_and_device_attachment(self):
        url = "https://www.52shuku.net/yanqing/17_b/bkfXm.html"
        status, _, body = self.req(
            "/api/batches",
            method="POST",
            data={"urls": [url, url]},
            origin=self.base,
        )
        self.assertEqual(status, 200, body)
        batch = json.loads(body)
        self.assertEqual(len(batch["jobs"]), 1)
        job = batch["jobs"][0]
        for _ in range(90):
            _, _, data = self.req("/api/batches/" + batch["id"])
            current = json.loads(data)["jobs"][0]
            if current["state"] == "completed":
                break
            if current["state"] in ("failed", "partial", "canceled"):
                self.fail(f"worker failed: {current}")
            time.sleep(0.1)
        else:
            self.fail("worker did not complete")
        self.assertEqual((current["done"], current["total"]), (2, 2))
        code, headers, text = self.req(current["download_url"])
        self.assertEqual(code, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertIn("filename*=", headers.get("Content-Disposition", ""))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("【小说简介】", text.decode("utf-8"))
        self.assertEqual(self.req(current["download_url"], authenticated=False)[0], 401)

    def test_daily_and_single_book_download(self):
        sys.path.insert(0,str(ROOT/'server'))
        from catalog.store import init, connect, upsert, publish, now
        from sources.base import Entry, Metadata
        path=self.root/'data'/'catalog.sqlite3'
        init(path)
        url="https://www.52shuku.net/yanqing/17_b/testBook.html"
        e=Entry('/yanqing/17_b/testBook.html',url,'测试书单','测试作者','仙侠','2026-11-24','latest_upload')
        with connect(path) as db:
            book_id=upsert(db,'52shuku',e,Metadata(e.title,e.author,e.category,'完整简介',e.uploaded),now().isoformat())
        day=now().date().isoformat()
        code,_,body=self.req('/api/daily/'+day)
        self.assertEqual(code,200)
        preview=json.loads(body)
        self.assertTrue(preview['preview'])
        self.assertFalse(preview['published'])
        self.assertEqual(preview['books'][0]['title'],'测试书单')
        from datetime import timedelta
        yesterday=(now().date()-timedelta(days=1)).isoformat()
        self.assertEqual(json.loads(self.req('/api/daily/'+yesterday)[2])['books'],[])
        with connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM edition_items').fetchone()[0],0)
        publish(path,day)
        self.assertEqual(self.req('/daily')[0],200)
        self.assertEqual(self.req('/api/daily/'+day,authenticated=False)[0],401)
        code,_,body=self.req('/api/daily/'+day)
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)['books'][0]['title'],'测试书单')
        self.assertFalse(json.loads(body)['preview'])
        self.assertTrue(json.loads(body)['published'])
        code,_,body=self.req('/api/books/'+str(book_id)+'/download',method='POST',data={'source':'52shuku'},origin=self.base)
        self.assertEqual(code,200,body)
        job=json.loads(body)['job']
        for _ in range(90):
            _,_,body=self.req('/api/catalog/jobs/'+job['id'])
            current=json.loads(body)
            if current['state']=='completed': break
            if current['state'] in ('failed','partial','canceled'): self.fail(str(current))
            time.sleep(.1)
        else: self.fail('Download wrapper did not complete')
        self.assertEqual(self.req(current['download_url'])[0],200)
        code,_,body=self.req('/api/books/'+str(book_id)+'/download',method='POST',data={'source':'52shuku'},origin=self.base)
        self.assertEqual(json.loads(body)['job']['id'],job['id'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
