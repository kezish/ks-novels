from .base import NotificationProvider
from catalog.store import connect
from urllib.parse import urljoin

def deliver(path, origin: str, provider: NotificationProvider | None = None):
    if provider is None:
        return 0
    with connect(path) as db:
        rows=db.execute("""SELECT o.day,e.partial,count(i.book_id) AS count FROM notification_outbox o
            JOIN editions e ON o.day=e.day LEFT JOIN edition_items i ON i.day=e.day
            WHERE o.state='pending' GROUP BY o.day ORDER BY o.day""").fetchall()
    sent=0
    for r in rows:
        provider.send(edition_day=r['day'],edition_url=urljoin(origin,'/daily/'+r['day']),count=r['count'],partial=bool(r['partial']))
        with connect(path) as db:
            db.execute("UPDATE notification_outbox SET state='sent' WHERE day=? AND state='pending'",(r['day'],))
        sent+=1
    return sent
