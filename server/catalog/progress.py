"""Live status of a catalog CLI run, shared across processes in SQLite."""
import fcntl
import json
from .store import connect, now
from sources.registry import registry

def lock_busy(path):
    # The CLI holds this lock from start to finish. Never create or alter
    # the lock file here; a read-only shared lock is enough to test it.
    try:
        with (path.parent/'catalog-sync.lock').open('rb') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True
    except FileNotFoundError:
        return False

class Progress:
    def __init__(self,path,adapters):
        self.path=path
        self.data={
            'started':now().isoformat(),'updated':now().isoformat(),
            'state':'running','current':None,
            'stages':[{'source':source,'label':getattr(adapter,'display_name',source),
                       'mode':mode,'state':'pending','page':0,'page_total':0,
                       'page_done':0,'processed':0,'added_sources':0,'errors':0,
                       'oldest':None,'latest':None,'cutoff':None}
                      for source,adapter in adapters.items() for mode in adapter.modes]}
        self.write()

    def write(self):
        self.data['updated']=now().isoformat()
        with connect(self.path) as db:
            db.execute('INSERT INTO sync_progress(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                       (json.dumps(self.data,ensure_ascii=False),))

    def start(self,index,cutoff):
        self.data['current']=index
        stage=self.data['stages'][index]
        stage.update(state='running',cutoff=cutoff)
        self.write()
        return stage

    def finish(self,stage,ok,error):
        stage.update(state='done' if ok else 'failed',error=error)
        self.write()

    def end(self):
        self.data['current']=None
        self.data['state']='failed' if any(s['state']=='failed' for s in self.data['stages']) else 'done'
        self.write()

def status(path):
    busy=lock_busy(path)
    with connect(path) as db:
        row=db.execute('SELECT payload FROM sync_progress WHERE id=1').fetchone()
        data=json.loads(row[0]) if row else None
        books=db.execute('SELECT count(*) FROM books').fetchone()[0]
        sources=[dict(r) for r in db.execute('SELECT source,count(*) AS count,min(uploaded) AS oldest,max(uploaded) AS latest FROM book_sources GROUP BY source')]
        recent=[dict(r) for r in db.execute('SELECT source,mode,started,ok,error FROM sync_runs WHERE id IN(SELECT max(id) FROM sync_runs GROUP BY source,mode) ORDER BY id')]
        last_book=db.execute('SELECT source,first_seen FROM book_sources ORDER BY first_seen DESC LIMIT 1').fetchone()
    adapters=registry()
    for s in sources:
        adapter=adapters.get(s['source'])
        s['label']=adapter.display_name if adapter else s['source']
    if data is None:
        data={'state':'running' if busy else ('done' if recent and all(r['ok'] for r in recent) and len(recent)==sum(len(a.modes) for a in adapters.values()) else ('failed' if recent else 'idle')),'started':None,'updated':None,'current':None,
              'stages':[], 'legacy':busy}
    elif data['state']=='running' and not busy:
        # Interrupted CLI: preserve partial counters without claiming completion.
        data['state']='interrupted'
    elif busy and data['state']!='running':
        # The lock also covers the brief daily-publish phase.
        data['publishing']=True
    data.update(books=books,sources=sources,last_runs=recent,lock_busy=busy)
    if data.get('legacy') and last_book:
        data['last_source']=last_book['source']
        data['updated']=last_book['first_seen']
    return data
