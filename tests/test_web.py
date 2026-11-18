"""Smoke and security regressions for the standalone ks-novels app."""
import base64
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
AUTH = "Basic " + base64.b64encode(
    b"ks:local-smoke-password-long-enough-for-integration"
).decode()


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
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = os.environ.copy()
        env.update(
            NOVELS_DATA_DIR=str(cls.root / "data"),
            NOVELS_PASSWORD_FILE=str(cls.passwd),
            NOVELS_YURI_BIN=str(cls.scraper),
            NOVELS_ORIGIN=cls.base,
        )
        cls.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "app:app", "--app-dir", str(ROOT / "server"),
             "--host", "127.0.0.1", "--port", str(cls.port), "--workers", "1"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
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
            headers["Authorization"] = AUTH
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

    def test_auth_and_static_assets(self):
        self.assertEqual(self.req("/healthz", authenticated=False)[0], 200)
        self.assertEqual(self.req("/api/batches", authenticated=False)[0], 401)
        self.assertEqual(self.req("/")[0], 200)
        status, _, html = self.req("/")
        self.assertIn("viewport", html.decode())
        self.assertIn("下载 TXT", html.decode())
        self.assertEqual(self.req("/assets/app.js")[0], 200)
        self.assertEqual(self.req("/assets/style.css")[0], 200)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
