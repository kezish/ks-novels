"""Periodic metadata-only sync with independent watermarks and recovery overlap."""
from datetime import datetime,timedelta
from .store import connect,init,now,existing,upsert
from sources.registry import registry

def sync(path, adapters=None, max_pages=120):
    init(path)
    adapters=registry() if adapters is None else adapters
    results=[]
    for source,adapter in adapters.items():
        for mode in adapter.modes:
            stamp=now()
            with connect(path) as db:
                last=db.execute('SELECT success_at FROM sync_state WHERE source=? AND mode=?',(source,mode)).fetchone()
            cutoff=((datetime.fromisoformat(last[0])-timedelta(hours=48)) if last else stamp-timedelta(days=7)).date().isoformat()
            ok=False
            error=''
            try:
                previous=set()
                failures=[]
                for page in range(max_pages):
                    entries,more=adapter.page(mode,page)
                    ids={e.external_id for e in entries}
                    if ids==previous: raise ValueError('分页重复，停止推进水位')
                    previous=ids
                    old=True
                    for e in entries:
                        if e.uploaded[:10]<cutoff: continue
                        old=False
                        with connect(path) as db:
                            known=existing(db,source,e.external_id)
                        try:
                            meta=adapter.metadata(e) if not known else None
                        except Exception as exc:
                            failures.append(f'{e.external_id}: {exc}')
                            continue
                        with connect(path) as db:
                            if known:
                                db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(source,e.external_id,mode))
                            else:
                                upsert(db,source,e,meta,now().isoformat())
                    if not more or (page>=1 and old):
                        ok=True
                        break
                if not ok: raise ValueError('扫描达到页数上限，水位未推进')
                if failures:
                    raise ValueError(f'{len(failures)} 本元数据未获取，水位未推进: '+ '; '.join(failures[:3]))
            except Exception as exc:
                ok=False
                error=str(exc)[:500]
            with connect(path) as db:
                if ok:
                    db.execute('INSERT INTO sync_state VALUES(?,?,?) ON CONFLICT(source,mode) DO UPDATE SET success_at=excluded.success_at',(source,mode,stamp.isoformat()))
                db.execute('INSERT INTO sync_runs(source,mode,started,ok,error) VALUES(?,?,?,?,?)',(source,mode,stamp.isoformat(),int(ok),error))
            results.append((source,mode,ok,error))
    return results
