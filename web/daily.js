"use strict";
const $=id=>document.getElementById(id);
let books=[], generation=0;
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function req(url,options){const r=await fetch(url,options);if(r.status===401){location.assign("/login?next="+encodeURIComponent(location.pathname));throw Error("登录已过期");}if(!r.ok)throw Error("请求失败 "+r.status);return r.json();}
function shift(day,n){const d=new Date(day+"T12:00:00Z");d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10);}
function filter(){return books.filter(b=>(!$("category").value||b.category===$("category").value)&&b.sources.some(s=>(!$("source").value||s.source===$("source").value)&&(!$("mode").value||s.modes.includes($("mode").value))));}
function directURL(b,s){return "/api/books/"+b.id+"/direct?"+new URLSearchParams({source:s.source,external_id:s.external_id});}
function fileLink(button,job){const a=el("a","保存 TXT","download");a.href=job.download_url;a.download=job.filename||"novel.txt";button.replaceWith(a);return a;}
async function queued(b,s,button){
  button.disabled=true;
  try{
    const r=await req("/api/books/"+b.id+"/download",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source:s.source,external_id:s.external_id})});
    let j=r.job;
    for(;;){
      if(j.state==="completed"){const a=fileLink(button,j);a.click();return;}
      if(!["queued","running","cancelling"].includes(j.state))throw Error(j.error||"任务失败");
      button.textContent=j.state==="queued"?"排队中…":"生成 TXT "+(j.total?Math.floor(100*j.done/j.total)+"%":"…");
      await new Promise(resolve=>setTimeout(resolve,2500));
      j=await req("/api/catalog/jobs/"+encodeURIComponent(j.id));
    }
  }catch(e){button.textContent="重试下载";button.title=e.message;button.disabled=false;}
}
function render(){
  const list=$("books"),open=new Set([...list.querySelectorAll("details[open]")].map(n=>n.dataset.id));list.replaceChildren();
  const visible=filter();
  for(const b of visible){
    const d=el("details");d.dataset.id=b.id;d.open=open.has(String(b.id));
    const summary=el("summary");summary.append(el("span",b.title,"title"),el("span",(b.category||"未分类")+" · "+(b.author||"未知作者"),"meta"));d.append(summary);
    const desc=el("div",b.description||"暂无简介","description");d.append(desc);
    d.append(el("div","来源："+b.sources.map(s=>s.label||s.source).join(" · "),"source"));
    const actions=el("div",undefined,"actions"),select=el("select");select.setAttribute("aria-label","下载书源");
    const sources=[...b.sources].sort((a,b)=>Number(b.download_kind==="direct")-Number(a.download_kind==="direct"));
    sources.forEach((s,i)=>{const o=el("option",s.label||s.source);o.value=i;select.append(o);});
    let current;
    function update(){
      if(current)current.remove();const s=sources[Number(select.value)||0];
      if(s.download_kind==="direct"){current=el("a","下载 TXT","download");current.href=directURL(b,s);}
      else{current=el("button","下载 TXT","download");if(s.download_kind==="queued"){const button=current;button.addEventListener("click",()=>queued(b,s,button));}else{current.disabled=true;current.textContent="暂不可下载";}}
      actions.prepend(current);
    }
    if(sources.length>1){actions.append(select);select.addEventListener("change",update);}
    update();d.append(actions);list.append(d);
  }
  $("status").textContent=($("status").dataset.base||"")+" · 显示 "+visible.length+" 本";
}
async function load(day){
  const g=++generation;$("date").value=day;$("status").textContent="读取书单…";
  try{const r=await req("/api/daily/"+day);if(g!==generation)return;books=r.books;
    $("status").dataset.base=r.published?(books.length+" 本"+(r.partial?" · 部分书源采集未完成":"")):"当天书单尚未生成";
    const source=$("source"),prior=source.value;source.replaceChildren(el("option","全部"));source.firstChild.value="";
    const available=new Map(books.flatMap(b=>b.sources.map(s=>[s.source,s.label||s.source])));
    for(const [id,name] of available){const o=el("option",name);o.value=id;source.append(o);}source.value=available.has(prior)?prior:"";
    const cat=$("category"),old=cat.value;cat.replaceChildren(el("option","全部"));cat.firstChild.value="";
    [...new Set(books.map(b=>b.category).filter(Boolean))].sort().forEach(c=>{const o=el("option",c);o.value=c;cat.append(o);});cat.value=old;if(!cat.value)cat.value="";
    history.replaceState(null,"","/daily/"+day);render();
  }catch(e){if(g===generation){$("status").textContent=e.message;$("books").replaceChildren();}}
}
$("prev").onclick=()=>load(shift($("date").value,-1));
$("next").onclick=()=>load(shift($("date").value,1));
$("date").onchange=()=>{if($("date").value)load($("date").value);};
for(const id of ["source","category","mode"])$(id).onchange=render;
(async()=>{const match=location.pathname.match(/^\/daily\/(\d{4}-\d{2}-\d{2})$/);if(match)return load(match[1]);
try{const r=await req("/api/daily");if(r.editions.length)return load(r.editions[0].day);}catch(e){}
const parts=new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Singapore",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));load(p.year+"-"+p.month+"-"+p.day);})();

const syncModes={latest_upload:"最新上传",new_book:"只看新书"};
function showSync(data){
  const progress=$("sync-progress"),stages=data.stages||[],state=data.state;
  const done=stages.filter(s=>s.state==="done").length;
  const ended=stages.filter(s=>s.state==="done"||s.state==="failed").length;
  const active=state==="running"?stages[data.current]:null;
  const labels={running:"同步中",done:"同步完成",failed:"同步部分失败",interrupted:"同步中断",idle:"等待首次同步"};
  $("sync-state").textContent=data.publishing?"正在发布书单":labels[state]||state;
  progress.max=Math.max(stages.length,1);
  // Source stages have different amounts of work. Never present this as a
  // percentage of all books; the upstream does not provide a cutoff count.
  if(state==="running"&&(!active||!stages.length))progress.removeAttribute("value");
  else if(stages.length)progress.value=ended;
  else progress.value=state==="done"?1:0;
  $("sync-info").textContent="已入库 "+data.books+" 本"+(stages.length?" · 已结束 "+ended+"/"+stages.length+" 阶段 · 成功 "+done:"");
  const src=(data.sources||[]).map(s=>s.label+" "+s.count+" 条").join(" · ");
  $("sync-page-wrap").hidden=!active;
  if(active){
    const bar=$("sync-page-progress");
    bar.max=Math.max(active.page_total,1);
    if(active.page_total)bar.value=active.page_done;else bar.removeAttribute("value");
    $("sync-page-label").textContent="当前页处理进度"+(active.page_total?" · "+active.page_done+"/"+active.page_total:" · 正在读取列表");
    $("sync-detail").textContent=active.label+" · "+(syncModes[active.mode]||active.mode)+" · 第 "+active.page+" 页"+
      (active.page_total?"（"+active.page_done+"/"+active.page_total+"）":"")+" · 已处理 "+active.processed+" 条"+
      (active.oldest?" · 扫描至 "+active.oldest.slice(0,10):"");
  }else{
    $("sync-detail").textContent=(data.legacy?"当前采集使用旧版本，详细阶段将在下一轮显示。 ":"")+src;
  }
  const container=$("sync-stages");container.replaceChildren();
  for(const s of stages){
    const row=el("div",undefined,"sync-row");
    row.append(el("strong",s.label+" · "+(syncModes[s.mode]||s.mode)),el("span",({pending:"等待",running:"采集中",done:"完成",failed:"失败"})[s.state]||s.state));
    row.append(el("p","已处理 "+s.processed+" 条 · 新增 "+s.added_sources+" 条 · 失败 "+s.errors+" 条"+(s.cutoff?" · 目标日期 "+s.cutoff:""),"sync-info"));
    if(s.state==="running"){
      const bar=el("progress");bar.max=Math.max(s.page_total,1);if(s.page_total)bar.value=s.page_done;
      bar.setAttribute("aria-label","当前页进度");row.append(bar);
    }
    if(s.error)row.append(el("p",s.error,"sync-error"));
    container.append(row);
  }
  if(!stages.length&&src)container.append(el("p",src,"sync-info"));
  if(data.updated){
    const time=new Date(data.updated);
    if(!Number.isNaN(time.getTime()))container.append(el("p","最后更新："+time.toLocaleString("zh-CN",{timeZone:"Asia/Singapore",hour12:false}),"sync-info"));
  }
}
let syncTimer,syncLoading=false;
async function refreshSync(){
  if(syncLoading)return;
  syncLoading=true;
  try{showSync(await req("/api/catalog/sync-status"));}
  catch(e){$("sync-state").textContent="状态暂不可用";}
  syncLoading=false;
  clearTimeout(syncTimer);
  syncTimer=setTimeout(refreshSync,document.hidden?30000:5000);
}
document.addEventListener("visibilitychange",()=>{if(!document.hidden){clearTimeout(syncTimer);refreshSync();}});
refreshSync();
