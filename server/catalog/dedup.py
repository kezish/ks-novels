"""Conservative title/author matching; uncertain matches stay separate."""
import re
import unicodedata
from difflib import SequenceMatcher

def normalize(value):
    s=unicodedata.normalize('NFKC',value).casefold().strip()
    s=re.sub(r'\s+','',s)
    s=re.sub(r'(?:【|\[|\()(?:完结|正文完|番外)[^】\])]*(?:】|\]|\))$','',s)
    return s.strip('《》')

def category(value):
    return unicodedata.normalize('NFKC',value).strip() or '未分类'

def title_similarity(a,b):
    return SequenceMatcher(None,a,b,autojunk=False).ratio()

def synopsis_similarity(a,b):
    def grams(s):
        s=''.join(c for c in unicodedata.normalize('NFKC',s) if c.isalnum()).casefold()
        return {s[i:i+3] for i in range(len(s)-2)}
    x,y=grams(a),grams(b)
    return 2*len(x&y)/(len(x)+len(y)) if x and y else 0.

def match(db, title, author, description):
    nt,na=normalize(title),normalize(author)
    exact=db.execute('SELECT id FROM books WHERE norm_title=? AND norm_author=?',(nt,na)).fetchall() if nt and na else []
    if len(exact)==1: return exact[0]['id'],[]
    candidates=[]
    if nt and na:
        for b in db.execute('SELECT id,norm_title,description FROM books WHERE norm_author=?',(na,)):
            sim=title_similarity(nt,b['norm_title'])
            if sim>=.85:
                desc=synopsis_similarity(description,b['description'])
                candidates.append((b['id'],sim,desc))
    # One unique high-confidence match. Short titles require exact matching.
    strong=[c for c in candidates if len(nt)>=8 and c[1]>=.95 and c[2]>=.85]
    return (strong[0][0],[]) if len(strong)==1 else (None,candidates)
