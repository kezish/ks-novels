"""Compatibility scheduler for existing installations before Nix timers activate."""
import asyncio
import contextlib
import logging
import os
import sys
from datetime import datetime,timedelta
from pathlib import Path
from .store import connect,init,now

log=logging.getLogger('novels.catalog')

def next_action(path, stamp):
    with connect(path) as db:
        edition=db.execute('SELECT 1 FROM editions WHERE day=?',(stamp.date().isoformat(),)).fetchone()
        latest=db.execute('SELECT max(started) FROM sync_runs').fetchone()[0]
    if stamp.hour>=8 and not edition:
        return 'daily'
    if not latest or datetime.fromisoformat(latest)<=stamp-timedelta(hours=1):
        return 'sync'
    return None

async def run(data: Path):
    path=data/'catalog.sqlite3'
    init(path)
    proc=None
    while True:
        try:
            action=next_action(path,now())
            if action:
                # Same persistent lock and CLI as the external timers.
                proc=await asyncio.create_subprocess_exec(sys.executable,'-m','catalog.cli',action,
                    cwd=str(Path(__file__).resolve().parent.parent),
                    env={**os.environ,'NOVELS_DATA_DIR':str(data)})
                code=await proc.wait()
                proc=None
                if code: log.warning('Catalog %s returned %s; inspect sync_runs',action,code)
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError): proc.terminate()
                await proc.wait()
            raise
        except Exception:
            log.exception('Catalog scheduler failed')
        await asyncio.sleep(60)
