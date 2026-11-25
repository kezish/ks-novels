from pathlib import Path
import json,tempfile,subprocess,os,shutil
root=Path(__file__).resolve().parents[1]/"web"
css=(root/"style.css").read_text()
mock=r"""
const requests=[],saved=[];
history.replaceState=()=>{}; // file fixture only; actual HTTP URL tested by backend
const url="https://www.52shuku.net/gl/a.html";
const row={site:"sk_shuku52",title:"测试长书名",author:"测试作者",book_url:url,category:"GL百合",published:"2026-11-24"};
let job={id:"testjob",url,title:"测试长书名",state:"queued",done:0,total:2};
window.fetch=async (u,o={})=>{
  requests.push([u,o.method,o.body]);
  let data={};
  if(u==="/api/batches"&&o.method==="POST")data={id:"testbatch",jobs:[job]};
  else if(u==="/api/batches/testbatch"){job={...job,state:"completed",done:2,filename:"test.txt",download_url:"/api/jobs/testjob/file"};data={id:"testbatch",jobs:[job]};}
  else if(u==="/api/batches")data={batches:requests.some(x=>x[1]==="POST"&&x[0]==="/api/batches")?[{id:"testbatch",jobs:[job]}]:[]};
  else if(u==="/api/sourcekit/sources")data={enabled:true,sites:{sk_shuku52:"52书库",sk_czbooks:"CZBooks",sk_jiujiu:"久久",sk_biquge5:"笔趣阁"}};
  else if(u==="/api/sourcekit/search")data={results:[{...row,title:"较旧",book_url:"https://www.52shuku.net/gl/old.html",published:"2025-01-01"},row],statuses:{sk_shuku52:{state:"completed"}}};
  else if(u.startsWith("/api/sourcekit/detail"))data={description:"长简介\n\n".repeat(25)};
  else if(u==="/api/daily")data={editions:[{day:"2026-11-24"}]};
  else if(u.startsWith("/api/daily/"))data={preview:true,books:[{id:1,title:row.title,author:row.author,category:"百合",description:"测试简介",sources:[{source:"52shuku",label:"52书库",external_id:"/gl/a.html",url,uploaded:"2026-11-25 15:00:00",download_kind:"queued",modes:["latest_upload"]}]}]};
  else if(u==="/api/catalog/sync-status")data={state:"done",books:1,stages:[],sources:[],last_sync:{completed:"2026-11-25T09:18:00Z",state:"done",new_books:3,new_sources:4},next_sync:"2026-11-25T10:15:00Z"};
  else throw Error("unhandled "+u);
  return new Response(JSON.stringify(data),{status:200});
};
HTMLAnchorElement.prototype.click=function(){saved.push(this.href)};
const errors=[];window.addEventListener("error",e=>errors.push(e.message));
window.addEventListener("unhandledrejection",e=>errors.push(String(e.reason)));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
function check(ok,msg){if(!ok)throw Error(msg)}
"""
search_test=r"""
window.addEventListener("load",async()=>{
  try{
    await delay(200);
    check(!document.querySelector("#sourcekit-panel").hidden,"search not initialized");
    document.querySelector("#open-sources").click();
    check(document.querySelector("#source-dialog").open,"dialog");
    document.querySelector("#source-done").click();
    document.querySelector("#search-keyword").value="测试";
    document.querySelector("#sourcekit-search").requestSubmit();
    await delay(150);
    check(JSON.parse(requests.filter(x=>x[0]==="/api/sourcekit/search").at(-1)[2]).order==="newest","default newest");
    const category=document.querySelector("#search-category");
    category.value="gl";category.dispatchEvent(new Event("change"));await delay(100);
    const query=JSON.parse(requests.filter(x=>x[0]==="/api/sourcekit/search").at(-1)[2]);
    check(query.category==="gl"&&query.order==="newest"&&Object.keys(query.cursors).length===0,"native filters restart");
    const cards=[...document.querySelectorAll(".book")];
    check(cards[0].dataset.published==="2026-11-24"&&cards[1].dataset.published==="2025-01-01","date order");
    const book=document.querySelector(".book"),action=book.querySelector(".book-action"),fold=book.querySelector("details");
    check(action.textContent==="下载 TXT","download label");
    fold.open=true;await delay(100);
    check(action.parentElement.className==="desc-actions","action moves");
    check(book.querySelector(".desc-copy").textContent.includes("长简介"),"description");
    action.click();action.click();await delay(3000);
    const posts=requests.filter(x=>x[0]==="/api/batches"&&x[1]==="POST");
    check(posts.length===1,"duplicate task");
    check(JSON.parse(posts[0][2]).urls.length===1,"single URL");
    check(document.querySelector("#links").value==="","manual list mutated");
    check(action.textContent==="保存 TXT","save button");
    check(saved.length===1,"auto save once");
    action.click();check(saved.length===2,"manual save fallback");
    fold.open=false;await delay(50);check(action.parentElement===book,"action restored");
    check(document.documentElement.scrollWidth<=innerWidth+1,"horizontal overflow");
    check(!errors.length,errors.join(";"));
    document.body.dataset.result="PASS:"+innerWidth;
  }catch(e){document.body.dataset.result="FAIL:"+e.message}
});
"""
daily_test=r"""
window.addEventListener("load",async()=>{
  try{
    await delay(250);
    const book=document.querySelector(".book"),action=book.querySelector(".book-action"),fold=book.querySelector("details");
    check(book.querySelector(".genre").textContent==="百合","category");
    check(document.querySelector("#sync-last").textContent==="2026-11-25 17:18","last sync");
    check(document.querySelector("#sync-next").textContent==="2026-11-25 18:15","next sync");
    check(document.querySelector("#sync-new").textContent==="3 本","new count");
    check(document.querySelector("#sync-total").textContent==="1 本","total count");
    check(book.querySelector(".upload-date").textContent==="2026-11-25 15:00","uploaded");
    fold.open=true;await delay(50);
    check(action.parentElement.className==="desc-actions","action moves");
    check(book.querySelector(".text-link").href===url,"original link");
    check(document.documentElement.scrollWidth<=innerWidth+1,"overflow");
    check(!errors.length,errors.join(";"));
    document.body.dataset.result="PASS:"+innerWidth;
  }catch(e){document.body.dataset.result="FAIL:"+e.message}
});
"""
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)
    for page in ("index","daily"):
        h=(root/(page+".html")).read_text()
        import re
        c=css if page=="index" else css+(root/"daily.css").read_text().split("\n",1)[1]
        h=re.sub(r'<link rel="stylesheet"[^>]+>',lambda _: "<style>"+c+"</style>",h)
        h=re.sub(r'<script src="[^"]+" defer></script>',lambda _: "<script>"+mock+"</script><script>"+(root/("app.js" if page=="index" else "daily.js")).read_text()+"</script><script>"+(search_test if page=="index" else daily_test)+"</script>",h)
        # Execute application only after DOM exists.
        scripts=re.findall(r"<script>.*?</script>",h,re.S)
        h=re.sub(r"<script>.*?</script>","",h,flags=re.S).replace("</body>","".join(scripts)+"</body>")
        (path/(page+".html")).write_text(h)
    frames="".join(f'<iframe style="width:{w}px;height:850px" src="{p}.html"></iframe>' for p in ("index","daily") for w in (320,390,430,1024))
    harness='<body>'+frames+'''<script>setTimeout(()=>{document.body.dataset.result=[...document.querySelectorAll("iframe")].map(f=>f.contentDocument.body.dataset.result).join("|")},6000)</script></body>'''
    (path/"test.html").write_text(harness)
    result=subprocess.run([os.environ.get("CHROME_BIN") or shutil.which("google-chrome-stable") or shutil.which("chromium") or "chromium","--headless","--disable-gpu","--no-first-run","--disable-background-networking","--allow-file-access-from-files","--user-data-dir="+str(path/"profile"),"--virtual-time-budget=10000","--dump-dom",(path/"test.html").as_uri()],capture_output=True,text=True,timeout=35)
    m=re.search(r'<body data-result="([^"]+)"',result.stdout)
    print("Chrome browser tests:",m[1] if m else result.stderr[-1000:])
    assert m and m[1].count("PASS:")==8 and "FAIL" not in m[1]
