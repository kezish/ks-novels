from datetime import date
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from .store import init,connect,books_for,now
from sources.registry import registry
from .progress import status as sync_status

class DownloadRequest(BaseModel):
    source: str | None = None
    external_id: str | None = None

def install(app, *, path, static, authorize, job_connect, book_dir, normalize_url, submit, submission, as_job):
    router=APIRouter(dependencies=[Depends(authorize)])
    def ready():
        path.parent.mkdir(parents=True,exist_ok=True)
        init(path)
    def selected(book_id, source, external_id=None):
        ready()
        with connect(path) as db:
            rows=db.execute('SELECT * FROM book_sources WHERE book_id=? ORDER BY CASE source WHEN "jiujiu" THEN 0 ELSE 1 END, source',(book_id,)).fetchall()
        row=next((r for r in rows if (source is None or r['source']==source) and (external_id is None or r['external_id']==external_id)),None)
        if row is None: raise HTTPException(404,'Book source not found')
        return row

    @router.get('/daily')
    @router.get('/daily/{day}')
    def daily_page(day: str | None=None):
        return FileResponse(static/'daily.html',media_type='text/html; charset=utf-8')
    @router.get('/assets/daily.js')
    def js():
        return FileResponse(static/'daily.js',media_type='text/javascript; charset=utf-8')
    @router.get('/assets/daily.css')
    def css():
        return FileResponse(static/'daily.css',media_type='text/css; charset=utf-8')
    @router.get('/api/catalog/sync-status')
    def catalog_sync_status():
        ready()
        return sync_status(path)

    @router.get('/api/daily')
    def editions():
        ready()
        with connect(path) as db:
            return {'editions':[dict(r) for r in db.execute('SELECT e.*,count(i.book_id) AS count FROM editions e LEFT JOIN edition_items i ON e.day=i.day GROUP BY e.day ORDER BY e.day DESC')]}
    @router.get('/api/daily/{day}')
    def edition(day: date):
        ready()
        with connect(path) as db:
            e=db.execute('SELECT * FROM editions WHERE day=?',(day.isoformat(),)).fetchone()
            return {'day':day.isoformat(),'published':bool(e),'partial':bool(e['partial']) if e else False,'books':books_for(db,day.isoformat()) if e else []}
    @router.get('/api/books/{book_id}/direct')
    def direct(book_id: int, source: str='jiujiu', external_id: str | None=None):
        row=selected(book_id,source,external_id)
        adapter=registry().get(row['source'])
        if adapter is None: raise HTTPException(404,'Unknown source')
        try: mode,url=adapter.download(row['url'])
        except Exception: raise HTTPException(502,'原站下载链接暂时不可用，请重试或切换书源')
        if mode!='direct': raise HTTPException(409,'Source requires a download job')
        return RedirectResponse(url,status_code=303)

    @router.post('/api/books/{book_id}/download')
    async def download(book_id: int, data: DownloadRequest):
        row=selected(book_id,data.source,data.external_id)
        adapter=registry().get(row['source'])
        if adapter is None: raise HTTPException(404,'Unknown source')
        if adapter.download_kind=='direct':
            from urllib.parse import urlencode
            return {'mode':'direct','url':f'/api/books/{book_id}/direct?'+urlencode({'source':row['source'],'external_id':row['external_id']})}
        mode,url=adapter.download(row['url'])
        if mode!='queued': raise HTTPException(409,'Unsupported download mode')
        url=normalize_url(url)
        with job_connect() as db:
            rows=db.execute("SELECT * FROM jobs WHERE url=? AND state IN ('queued','running','completed') ORDER BY created_at DESC",(url,)).fetchall()
        for job in rows:
            if job['state']=='completed':
                file=Path(job['file_path']).resolve()
                if not file.is_file() or file.parent!=book_dir.resolve(): continue
            return {'mode':'queued','job':as_job(job)}
        result=await submit(submission(urls=[url]),True)
        return {'mode':'queued','job':result['jobs'][0]}
    @router.get('/api/catalog/jobs/{job_id}')
    def status(job_id: str):
        with job_connect() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row: raise HTTPException(404,'Job not found')
        return as_job(row)
    app.include_router(router)
