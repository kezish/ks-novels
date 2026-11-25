"""Catalog persistence; original source identity never discarded."""
import sqlite3
from .dedup import normalize as normalized, category, match
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ=ZoneInfo('Asia/Singapore')
def now(): return datetime.now(TZ)
@contextmanager
def connect(path):
    db=sqlite3.connect(path,timeout=20)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally: db.close()

def init(path):
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript("""
        CREATE TABLE IF NOT EXISTS books(id INTEGER PRIMARY KEY, title TEXT NOT NULL, author TEXT NOT NULL,
          category TEXT NOT NULL, description TEXT NOT NULL, norm_title TEXT NOT NULL, norm_author TEXT NOT NULL,
          first_seen TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS books_match ON books(norm_title,norm_author);
        CREATE INDEX IF NOT EXISTS books_author ON books(norm_author);
        CREATE TABLE IF NOT EXISTS dedup_candidates(book_id INTEGER REFERENCES books(id), candidate_id INTEGER REFERENCES books(id),
          title_score REAL NOT NULL, description_score REAL NOT NULL, PRIMARY KEY(book_id,candidate_id));
        CREATE TABLE IF NOT EXISTS book_sources(source TEXT NOT NULL, external_id TEXT NOT NULL,
          book_id INTEGER NOT NULL REFERENCES books(id), url TEXT NOT NULL, uploaded TEXT NOT NULL,
          title TEXT NOT NULL, author TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
          first_seen TEXT NOT NULL, PRIMARY KEY(source,external_id));
        CREATE TABLE IF NOT EXISTS memberships(source TEXT NOT NULL, external_id TEXT NOT NULL, mode TEXT NOT NULL,
          PRIMARY KEY(source,external_id,mode), FOREIGN KEY(source,external_id) REFERENCES book_sources(source,external_id));
        CREATE TABLE IF NOT EXISTS sync_state(source TEXT, mode TEXT, success_at TEXT NOT NULL,
          PRIMARY KEY(source,mode));
        CREATE TABLE IF NOT EXISTS sync_progress(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_runs(id INTEGER PRIMARY KEY, source TEXT, mode TEXT, started TEXT, ok INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS editions(day TEXT PRIMARY KEY, created TEXT NOT NULL, partial INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS edition_items(day TEXT REFERENCES editions(day), book_id INTEGER REFERENCES books(id),
          PRIMARY KEY(day,book_id), UNIQUE(book_id));
        CREATE TABLE IF NOT EXISTS notification_outbox(day TEXT PRIMARY KEY REFERENCES editions(day),
          state TEXT NOT NULL DEFAULT 'pending');
        """)

def existing(db,source,external_id):
    return db.execute('SELECT * FROM book_sources WHERE source=? AND external_id=?',(source,external_id)).fetchone()

def upsert(db,source,entry,meta,seen):
    row=existing(db,source,entry.external_id)
    if row: bid=row['book_id']
    else:
        nt,na=normalized(meta.title),normalized(meta.author)
        bid,candidates=match(db,meta.title,meta.author,meta.description)
        if bid is None:
            bid=db.execute('INSERT INTO books(title,author,category,description,norm_title,norm_author,first_seen) VALUES(?,?,?,?,?,?,?)',
                (meta.title,meta.author,category(meta.category),meta.description,nt,na,seen)).lastrowid
            db.executemany("INSERT OR IGNORE INTO dedup_candidates VALUES(?,?,?,?)",[(bid,*c) for c in candidates])
        db.execute('INSERT INTO book_sources VALUES(?,?,?,?,?,?,?,?,?,?)',
            (source,entry.external_id,bid,entry.url,meta.uploaded,meta.title,meta.author,meta.category,meta.description,seen))
        db.execute('UPDATE books SET description=CASE WHEN length(description)<? THEN ? ELSE description END, category=CASE WHEN category IN ("","未分类") THEN ? ELSE category END WHERE id=?',
            (len(meta.description),meta.description,category(meta.category),bid))
    db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(source,entry.external_id,entry.mode))
    return bid

def publish(path, day=None):
    stamp=now().isoformat()
    day=day or stamp[:10]
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM editions WHERE day=?',(day,)).fetchone(): return False
        from sources.registry import registry
        required={(a.source_id,mode) for a in registry().values() for mode in a.modes}
        state={(r['source'],r['mode']):r for r in db.execute('SELECT * FROM sync_runs WHERE id IN(SELECT max(id) FROM sync_runs GROUP BY source,mode)')}
        partial=any(k not in state or not state[k]['ok'] or datetime.fromisoformat(state[k]['started'])<now()-timedelta(hours=3) for k in required)
        db.execute('INSERT INTO editions VALUES(?,?,?)',(day,stamp,int(partial)))
        db.execute('INSERT INTO edition_items SELECT ?,id FROM books WHERE first_seen<=? AND id NOT IN(SELECT book_id FROM edition_items)',(day,stamp))
        db.execute('INSERT INTO notification_outbox(day) VALUES(?)',(day,))
    return True

def books_for(db,day,*,preview=False):
    if preview:
        rows=db.execute("""SELECT b.* FROM books b WHERE NOT EXISTS
          (SELECT 1 FROM edition_items e WHERE e.book_id=b.id)
          ORDER BY b.first_seen DESC,b.id DESC""").fetchall()
    else:
        rows=db.execute('''SELECT b.* FROM books b JOIN edition_items e ON b.id=e.book_id
      WHERE e.day=? ORDER BY b.first_seen DESC,b.id DESC''',(day,)).fetchall()
    from sources.registry import registry
    adapters=registry()
    result=[]
    for r in rows:
        b={k:r[k] for k in ('id','title','author','category','description','first_seen')}
        b['sources']=[]
        for s in db.execute('SELECT source,external_id,uploaded,url FROM book_sources WHERE book_id=? ORDER BY source',(r['id'],)):
            item=dict(s)
            adapter=adapters.get(s['source'])
            item['label']=adapter.display_name if adapter else s['source']
            item['download_kind']=adapter.download_kind if adapter else 'unavailable'
            item['modes']=[m[0] for m in db.execute('SELECT mode FROM memberships WHERE source=? AND external_id=?',(s['source'],s['external_id']))]
            b['sources'].append(item)
        b["uploaded"]=max((s["uploaded"] for s in b["sources"] if s["uploaded"]),default="")
        result.append(b)
    return sorted(result,key=lambda b:(b["uploaded"],b["id"]),reverse=True)
