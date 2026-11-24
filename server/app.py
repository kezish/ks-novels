"""Single-user, local-first TXT job queue for 52shuku; no public file listing."""
import asyncio
import contextlib
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from session_auth import BrowserAuth
from pydantic import BaseModel, Field
import sourcekit_bridge as sourcekit

DATA = Path(os.environ.get("NOVELS_DATA_DIR", "/var/lib/novels-web"))
BOOKS = DATA / "books"
DB = DATA / "jobs.sqlite3"
STATIC = Path(__file__).resolve().parent.parent / "web"
YURI = os.environ.get("NOVELS_YURI_BIN", "yuri-scraper")
SITE = os.environ.get("NOVELS_ORIGIN", "https://novels.mtschema.com")
MAX_BATCH = 30
KEEP_SECONDS = 12 * 3600
MAX_PENDING = 100
WORKER_TIMEOUT = 3600
STATUS_RE = re.compile(r"^(?:downloading|exporting|completed): (\d+)/(\d+) completed, (\d+) failed")
browser_auth = BrowserAuth(DATA, STATIC, SITE)
running_job = None
running_process = None
worker_task = None
cleaner_task = None


@contextmanager
def connect():
    conn = sqlite3.connect(DB, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    BOOKS.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY, created_at REAL NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, url TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            file_path TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, finished_at REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(batch_id) REFERENCES batches(id))""")
        db.execute("UPDATE jobs SET state='queued' WHERE state IN ('running','cancelling')")


def secret():
    path = os.environ.get("NOVELS_PASSWORD_FILE")
    if not path:
        credentials = os.environ.get("CREDENTIALS_DIRECTORY")
        path = str(Path(credentials) / "password") if credentials else ""
    if not path:
        raise RuntimeError("NOVELS_PASSWORD_FILE or CREDENTIALS_DIRECTORY is required")
    password = Path(path).read_text(encoding="utf-8").strip()
    if len(password) < 24:
        raise RuntimeError("Novels password must contain at least 24 characters")
    return password


def authorize(request: Request):
    return browser_auth.authorize(request)


def normalize_url(raw: str):
    if len(raw) > 1200:
        raise ValueError("URL too long")
    try:
        u = urlsplit(raw.strip())
        if u.hostname not in ("52shuku.net", "www.52shuku.net") and sourcekit.BIN:
            return sourcekit.normalize_external(raw.strip())
        if (u.scheme != "https" or u.username is not None or u.password is not None
                or u.hostname not in ("52shuku.net", "www.52shuku.net")
                or u.port not in (None, 443) or u.query):
            raise ValueError("Only HTTPS 52shuku book URLs are accepted")
        if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\.html", u.path):
            raise ValueError("Please paste a 52shuku book homepage URL")
        if re.search(r"_\d+\.html$", u.path):
            # Reading pages are not accepted; users must submit the book homepage.
            raise ValueError("Please paste the main book URL, not a chapter page")
        return urlunsplit(("https", "www.52shuku.net", u.path, "", ""))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


class Submission(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=MAX_BATCH)


def as_job(row):
    data = dict(row)
    data["download_url"] = f"/api/jobs/{row['id']}/file" if row["state"] == "completed" else None
    data["filename"] = Path(row["file_path"]).name if row["state"] == "completed" else None
    data.pop("file_path", None)
    return data


def batch_data(batch_id: str):
    with connect() as db:
        if not db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone():
            raise HTTPException(404, "Batch not found")
        rows = db.execute("SELECT * FROM jobs WHERE batch_id=? ORDER BY created_at,id", (batch_id,)).fetchall()
    return {"id": batch_id, "jobs": [as_job(r) for r in rows]}


async def worker():
    global running_job, running_process
    while True:
        with connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running',error='' WHERE id=?", (row["id"],))
        if row is None:
            await asyncio.sleep(0.8)
            continue
        running_job = row["id"]
        progress_error = ""
        path = ""
        try:
            args = sourcekit.command(row["url"], BOOKS, DATA) if sourcekit.BIN else None
            if args is None:
                args = [YURI, "download", row["url"], "--source", "52shuku", "--format", "txt",
                        "--concurrency", "4", "--rate-limit", "2.0", "--retry-count", "3", "--output-dir", str(BOOKS)]
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={k: v for k, v in os.environ.items() if k.lower() not in (
                    "http_proxy", "https_proxy", "all_proxy")},
            )
            running_process = proc

            async def collect():
                nonlocal progress_error
                async for b in proc.stderr:
                    line = b.decode("utf-8", "replace").strip()
                    match = STATUS_RE.match(line)
                    if match:
                        with connect() as db:
                            db.execute("UPDATE jobs SET done=?,total=?,failed=? WHERE id=?",
                                       (*map(int, match.groups()), row["id"]))
                    elif line:
                        progress_error = (progress_error + "\n" + line)[-1800:]
                out = (await proc.stdout.read()).decode("utf-8", "replace").strip()
                return out

            output = await asyncio.wait_for(collect(), timeout=WORKER_TIMEOUT)
            code = await proc.wait()
            if code == 0:
                candidates = [Path(v.strip()) for v in output.splitlines() if v.strip().endswith(".txt")]
                if candidates:
                    final = candidates[-1].resolve()
                    if final.is_file() and final.parent == BOOKS.resolve():
                        path = str(final)
            with connect() as db:
                state = db.execute("SELECT state FROM jobs WHERE id=?", (row["id"],)).fetchone()["state"]
                if state == "cancelling":
                    final_state = "canceled"
                elif code == 0 and path:
                    final_state = "completed"
                elif code == 3:
                    final_state = "partial"
                else:
                    final_state = "failed"
                title = Path(path).stem.rsplit("-", 1)[0] if path else ""
                db.execute("""UPDATE jobs SET state=?,file_path=?,title=?,error=?,finished_at=?
                           WHERE id=?""", (final_state, path, title, "" if final_state == "completed"
                           else (progress_error or f"Downloader exited with {code}")[-900:],
                           time.time(), row["id"]))
        except asyncio.CancelledError:
            if running_process is not None and running_process.returncode is None:
                running_process.kill()
                await running_process.wait()
            raise
        except Exception as exc:
            if running_process is not None and running_process.returncode is None:
                running_process.kill()
                await running_process.wait()
            with connect() as db:
                db.execute("UPDATE jobs SET state='failed',error=?,finished_at=? WHERE id=?",
                           (str(exc)[-900:], time.time(), row["id"]))
        finally:
            running_process = None
            running_job = None


async def cleaner():
    while True:
        await asyncio.sleep(600)
        cutoff = time.time() - KEEP_SECONDS
        with connect() as db:
            old = db.execute("""SELECT id,url,file_path FROM jobs
                WHERE state IN ('completed','failed','partial','canceled') AND finished_at < ?""",
                             (cutoff,)).fetchall()
            for row in old:
                with connect() as db:
                    # A completed file can be reused by a later job.
                    still_used = db.execute(
                        "SELECT 1 FROM jobs WHERE file_path=? AND id!=? AND state='completed' LIMIT 1",
                        (row["file_path"], row["id"])).fetchone() if row["file_path"] else None
                    db.execute("UPDATE jobs SET state='expired',file_path='' WHERE id=?", (row["id"],))
                if not still_used and row["file_path"]:
                    file = Path(row["file_path"]).resolve()
                    if file.parent == BOOKS.resolve():
                        with contextlib.suppress(FileNotFoundError):
                            file.unlink()
        # Remove expired unreferenced chapter caches without touching active downloads.
        if running_job is None:
            cache_root = BOOKS / ".cache"
            if cache_root.is_dir():
                for folder in cache_root.iterdir():
                    if folder.is_dir() and folder.stat().st_mtime < cutoff:
                        shutil.rmtree(folder, ignore_errors=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    browser_auth.init(secret())
    global worker_task, cleaner_task
    worker_task = asyncio.create_task(worker())
    cleaner_task = asyncio.create_task(cleaner())
    tasks = [worker_task, cleaner_task]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Novels TXT", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def secure_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self';"
        " connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'"
    )
    return response


browser_auth.install(app)


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index(_: bool = Depends(authorize)):
    return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")


@app.get("/assets/app.js")
async def javascript(_: bool = Depends(authorize)):
    return FileResponse(STATIC / "app.js", media_type="text/javascript; charset=utf-8")


@app.get("/assets/style.css")
async def stylesheet(_: bool = Depends(authorize)):
    return FileResponse(STATIC / "style.css", media_type="text/css; charset=utf-8")



class SearchSubmission(BaseModel):
    keyword: str = Field(min_length=1,max_length=100)
    sites: list[str] = Field(min_length=1,max_length=4)


@app.get("/api/sourcekit/sources")
async def sourcekit_sources(_: bool = Depends(authorize)):
    return {"enabled": bool(sourcekit.BIN), "sites":sourcekit.ALL_SITES if sourcekit.BIN else {}, "use_52":sourcekit.USE_52}


@app.post("/api/sourcekit/search")
async def sourcekit_search(data: SearchSubmission, _: bool = Depends(authorize)):
    try:
        return await sourcekit.search(data.keyword,data.sites,DATA/"catalog.sqlite3")
    except asyncio.TimeoutError as exc:
        raise HTTPException(504,"书源搜索超时") from exc
    except OSError as exc:
        raise HTTPException(503,"SourceKit 不可用") from exc


@app.post("/api/batches")
async def submit(data: Submission, _: bool = Depends(authorize)):
    normalized = []
    try:
        for u in data.urls:
            clean = normalize_url(u)
            if clean not in normalized:
                normalized.append(clean)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not normalized:
        raise HTTPException(422, "No URLs")
    with connect() as db:
        pending = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
        if pending + len(normalized) > MAX_PENDING:
            raise HTTPException(429, "The download queue is full")
        batch = uuid.uuid4().hex
        now = time.time()
        db.execute("INSERT INTO batches VALUES (?,?)", (batch, now))
        for i, url in enumerate(normalized):
            db.execute("""INSERT INTO jobs(id,batch_id,url,state,created_at)
                       VALUES (?,?,?,?,?)""", (uuid.uuid4().hex, batch, url, "queued", now + i * 0.001))
    return batch_data(batch)


@app.get("/api/batches")
async def batches(_: bool = Depends(authorize)):
    with connect() as db:
        rows = db.execute("SELECT id FROM batches ORDER BY created_at DESC LIMIT 15").fetchall()
    return {"batches": [batch_data(r["id"]) for r in rows]}


@app.get("/api/batches/{batch_id}")
async def one_batch(batch_id: str, _: bool = Depends(authorize)):
    return batch_data(batch_id)


@app.get("/api/jobs/{job_id}/file")
async def download(job_id: str, _: bool = Depends(authorize)):
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or row["state"] != "completed":
        raise HTTPException(404, "File not ready")
    file = Path(row["file_path"]).resolve()
    if not file.is_file() or file.parent != BOOKS.resolve() or file.suffix != ".txt":
        raise HTTPException(404, "File expired")
    return FileResponse(file, media_type="application/octet-stream", filename=file.name)


@app.post("/api/jobs/{job_id}/retry")
async def retry(job_id: str, _: bool = Depends(authorize)):
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        if row["state"] not in ("failed", "partial", "canceled", "expired"):
            raise HTTPException(409, "Job is not retryable")
        db.execute("""UPDATE jobs SET state='queued',done=0,total=0,failed=0,
                   error='',file_path='',finished_at=0 WHERE id=?""", (job_id,))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str, _: bool = Depends(authorize)):
    with connect() as db:
        row = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        if row["state"] == "queued":
            db.execute("UPDATE jobs SET state='canceled',finished_at=? WHERE id=?",
                       (time.time(), job_id))
        elif row["state"] == "running":
            db.execute("UPDATE jobs SET state='cancelling' WHERE id=?", (job_id,))
            if running_job == job_id and running_process and running_process.returncode is None:
                running_process.terminate()
        else:
            raise HTTPException(409, "Job cannot be canceled")
    return {"ok": True}

# Daily catalog is isolated from the existing queue schema and worker.
from catalog.routes import install as install_catalog
install_catalog(app, path=DATA / "catalog.sqlite3", static=STATIC,
                authorize=authorize, job_connect=connect, book_dir=BOOKS,
                normalize_url=normalize_url, submit=submit, submission=Submission,
                as_job=as_job)
