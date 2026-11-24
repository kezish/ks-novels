"""Periodic metadata-only sync with independent watermarks and recovery overlap."""
from datetime import datetime,timedelta
from .store import connect,init,now,existing,upsert
from sources.registry import registry
from .progress import Progress

def sync(path, adapters=None, max_pages=120):
    init(path)
    adapters=registry() if adapters is None else adapters
    results=[]
    progress=Progress(path,adapters)
    stage_index=0
    for source,adapter in adapters.items():
        for mode in adapter.modes:
            stamp=now()
            with connect(path) as db:
                last=db.execute('SELECT success_at FROM sync_state WHERE source=? AND mode=?',(source,mode)).fetchone()
            cutoff=((datetime.fromisoformat(last[0])-timedelta(hours=48)) if last else stamp-timedelta(days=7)).date().isoformat()
            stage=progress.start(stage_index,cutoff)
            stage_index+=1
            ok=False
            error=''
            try:
                previous=set()
                failures=[]
                for page in range(max_pages):
                    stage.update(page=page+1,page_done=0,page_total=0)
                    progress.write()
                    entries,more=adapter.page(mode,page)
                    stage["page_total"]=len(entries)
                    progress.write()
                    ids={e.external_id for e in entries}
                    if ids==previous: raise ValueError('分页重复，停止推进水位')
                    previous=ids
                    old=True
                    for e in entries:
                        stage['page_done']+=1
                        stage['oldest']=min(stage['oldest'] or e.uploaded,e.uploaded)
                        stage['latest']=max(stage['latest'] or e.uploaded,e.uploaded)
                        if e.uploaded[:10]<cutoff:
                            progress.write()
                            continue
                        stage['processed']+=1
                        old=False
                        with connect(path) as db:
                            known=existing(db,source,e.external_id)
                        try:
                            meta=adapter.metadata(e) if not known else None
                        except Exception as exc:
                            failures.append(f'{e.external_id}: {exc}')
                            stage['errors']+=1
                            progress.write()
                            continue
                        with connect(path) as db:
                            if known:
                                db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(source,e.external_id,mode))
                            else:
                                upsert(db,source,e,meta,now().isoformat())
                                stage["added_sources"]+=1
                        progress.write()
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
            progress.finish(stage,ok,error)
            results.append((source,mode,ok,error))
    progress.end()
    return results
