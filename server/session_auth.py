"""Single-user, revocable persistent browser sessions. No plaintext token storage."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from html import escape
from urllib.parse import parse_qs, urlencode
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

DAY = 86400
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
SAFE_PAGE = re.compile(r"/(?:daily(?:/\d{4}-\d{2}-\d{2})?)?")
def safe_next(value):
    return value if SAFE_PAGE.fullmatch(value or "") else "/daily"

def env_int(name, default, minimum, maximum):
    val = int(os.environ.get(name, str(default)))
    if not minimum <= val <= maximum:
        raise ValueError(f"Invalid {name}")
    return val

class SessionStore:
    def __init__(self, path, password, *, absolute=180*DAY, idle=30*DAY,
                 limit=10, window=900, clock=time.time):
        self.path, self.password, self.clock = path, password, clock
        self.absolute, self.idle, self.limit, self.window = absolute, idle, limit, window
        self.version = hmac.digest(password.encode(), b"ks-novels/session-version/v1", "sha256").hex()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, created REAL NOT NULL,
                    last_seen REAL NOT NULL, expires REAL NOT NULL, version TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS failures(
                    bucket TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
            """)
            self.prune(db, self.clock())

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def prune(self, db, now):
        db.execute("DELETE FROM sessions WHERE expires<=? OR last_seen<=? OR version!=?",
                   (now, now-self.idle, self.version))
        db.execute("DELETE FROM failures WHERE started<=?", (now-self.window,))

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def valid(self, token):
        if not token or not TOKEN_RE.fullmatch(token):
            return False
        now = self.clock()
        key = self.token_hash(token)
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE token_hash=?", (key,)).fetchone()
            if (not row or row["version"] != self.version or
                row["expires"]<=now or row["created"]+self.absolute<=now or
                row["last_seen"]+self.idle<=now):
                return False
            # Avoid writing SQLite on every polling request.
            if row["last_seen"] < now-min(300, self.idle/10):
                db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now,key))
            return True

    def login(self, username, password, ip, previous=None):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.prune(db, now)
            keys = ("ip:"+ip, "global")
            caps = (self.limit, self.limit*10)
            for key,cap in zip(keys,caps):
                r = db.execute("SELECT * FROM failures WHERE bucket=?", (key,)).fetchone()
                if r and r["count"]>=cap:
                    return None, max(1,int(r["started"]+self.window-now)+1)
            good = hmac.compare_digest(username.encode(), b"ks") & hmac.compare_digest(
                password.encode(), self.password.encode())
            if not good:
                for key in keys:
                    db.execute("""INSERT INTO failures VALUES(?,?,1)
                        ON CONFLICT(bucket) DO UPDATE SET count=count+1""", (key,now))
                return None, 0
            db.execute("DELETE FROM failures WHERE bucket=?", (keys[0],))
            if previous and TOKEN_RE.fullmatch(previous):
                db.execute("DELETE FROM sessions WHERE token_hash=?", (self.token_hash(previous),))
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                       (self.token_hash(token),now,now,now+self.absolute,self.version))
            return token, 0

    def revoke(self, token, all_sessions=False):
        if not token or not TOKEN_RE.fullmatch(token):
            return
        with self.connect() as db:
            if all_sessions:
                db.execute("DELETE FROM sessions")
            else:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (self.token_hash(token),))

class BrowserAuth:
    def __init__(self, data, static, origin):
        self.data, self.static, self.origin = data, static, origin.rstrip("/")
        self.secure = self.origin.startswith("https://")
        self.cookie = "__Host-novels_session" if self.secure else "novels_session"
        self.store = None

    def init(self, password):
        absolute=env_int("NOVELS_SESSION_MAX_DAYS",180,1,365)*DAY
        idle=env_int("NOVELS_SESSION_IDLE_DAYS",30,1,365)*DAY
        self.store=SessionStore(self.data/"sessions.sqlite3",password,
            absolute=absolute,idle=min(idle,absolute),
            limit=env_int("NOVELS_LOGIN_MAX_FAILURES",10,1,100),
            window=env_int("NOVELS_LOGIN_WINDOW_SECONDS",900,60,86400))

    def same_origin(self, request):
        if request.headers.get("origin") != self.origin:
            raise HTTPException(403, "Invalid request origin")

    def valid(self, request):
        return self.store.valid(request.cookies.get(self.cookie))

    def authorize(self, request: Request):
        if not self.valid(request):
            if request.method == "GET" and SAFE_PAGE.fullmatch(request.url.path):
                raise HTTPException(303, headers={"Location":"/login?"+urlencode({"next":request.url.path})})
            raise HTTPException(401,"Authentication required")
        if request.method not in ("GET","HEAD","OPTIONS"):
            self.same_origin(request)
        return True

    def page(self, next_page, error="", status=200, retry=0):
        html=(self.static/"login.html").read_text(encoding="utf-8")
        html=html.replace("{{NEXT}}",escape(safe_next(next_page),quote=True)).replace("{{ERROR}}",escape(error))
        return HTMLResponse(html,status_code=status,headers={"Retry-After":str(retry)} if retry else {})

    def install(self, app):
        @app.get("/login")
        def login_page(request: Request, next: str="/daily"):
            if self.valid(request):
                return RedirectResponse(safe_next(next),303)
            return self.page(next)

        @app.get("/assets/login.css")
        def login_css():
            return FileResponse(self.static/"login.css",media_type="text/css; charset=utf-8")

        @app.post("/login")
        async def login(request: Request):
            self.same_origin(request)
            if request.headers.get("content-type","").split(";")[0].strip()!="application/x-www-form-urlencoded":
                raise HTTPException(415,"Form required")
            raw=bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw)>8192:
                    raise HTTPException(413,"Form too large")
            try:
                data=parse_qs(raw.decode("utf-8"),keep_blank_values=True,max_num_fields=8,errors="strict")
            except (ValueError,UnicodeError):
                raise HTTPException(400,"Invalid form")
            value=lambda key, default="": data.get(key,[default])[0]
            username,password,next_page=value("username"),value("password"),safe_next(value("next"))
            if len(username)>128 or len(password)>1024:
                raise HTTPException(400,"Invalid credentials")
            # Uvicorn trusts forwarding headers only from the loopback Caddy proxy.
            ip=request.client.host if request.client else "unknown"
            token,retry=self.store.login(username,password,ip,request.cookies.get(self.cookie))
            if not token:
                return self.page(next_page,"尝试次数过多，请稍后再试。" if retry else "用户名或密码错误。",
                                 429 if retry else 401,retry)
            response=RedirectResponse(next_page,303)
            response.set_cookie(self.cookie,token,max_age=self.store.absolute,
                path="/",secure=self.secure,httponly=True,samesite="lax")
            return response

        @app.post("/logout")
        def logout(request: Request):
            self.same_origin(request)
            self.store.revoke(request.cookies.get(self.cookie))
            response=RedirectResponse("/login",303)
            response.delete_cookie(self.cookie,path="/",secure=self.secure,httponly=True,samesite="lax")
            return response
