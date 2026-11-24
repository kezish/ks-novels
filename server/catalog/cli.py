import argparse
import fcntl
import os
from pathlib import Path
from .store import init,publish
from .sync import sync

def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['sync','publish','daily'])
    a=p.parse_args()
    data=Path(os.environ.get('NOVELS_DATA_DIR','/var/lib/novels-web'))
    data.mkdir(parents=True,exist_ok=True)
    db=data/'catalog.sqlite3'
    with (data/'catalog-sync.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return run(a.action,db)

def run(action,db):
    init(db)
    if action in ('sync','daily'):
        results=sync(db)
        for r in results: print(*r,flush=True)
    else: results=[]
    if action in ('publish','daily'): print('Published:',publish(db),flush=True)
    return int(any(not r[2] for r in results))
if __name__=='__main__': raise SystemExit(main())
