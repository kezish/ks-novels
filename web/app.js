"use strict";
const form = document.querySelector("#submit-form");
const links = document.querySelector("#links");
const msg = document.querySelector("#message");
const batchesNode = document.querySelector("#batches");
const folderActions = document.querySelector("#folder-actions");
const folderHint = document.querySelector("#save-hint");
const submitButton = document.querySelector("#submit");
const manualSubmitButton = document.querySelector("#queue-only");
let batches = [];
const singleJobs = new Map();
const pendingSingle = new Set();
let toastTimer;
function notify(text) {
  const n=document.querySelector("#toast");
  if (!n) return;
  n.textContent=text;n.hidden=false;
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>n.hidden=true,4000);
}
function updateSingleButtons() {
  const count=document.querySelector("#nav-count");
  if(count){const n=batches.flatMap(b=>b.jobs).filter(j=>["queued","running","cancelling"].includes(j.state)).length;count.textContent=n;count.hidden=!n;}
  if(!document.querySelectorAll)return;
  for(const btn of document.querySelectorAll("button[data-single-url]")){
    const url=btn.dataset.singleUrl,job=singleJobs.get(url);
    btn.disabled=pendingSingle.has(url)||!!(job&&["queued","running","cancelling"].includes(job.state));
    btn.classList.toggle("done",job?.state==="completed");
    btn.textContent=pendingSingle.has(url)?"提交中…":job?.state==="completed"?"保存 TXT":job?.state==="running"?"生成 "+(job.total?Math.floor(100*job.done/job.total)+"%":"…"):job?.state==="queued"?"排队中…":job?.state==="cancelling"?"取消中…":job?"重试下载":"下载 TXT";
    btn.title=job?.error||"";
  }
}
function saveSingle(job) {
  const a=el("a");a.href=job.download_url;a.download=job.filename||"novel.txt";
  document.body.append(a);a.click();a.remove();
}
async function downloadSingle(url) {
  if(pendingSingle.has(url))return;
  const previous=singleJobs.get(url);
  if(previous?.state==="completed"){saveSingle(previous);return;}
  if(previous&&["queued","running","cancelling"].includes(previous.state))return;
  pendingSingle.add(url);updateSingleButtons();
  try {
    const data=await request("/api/batches",{method:"POST",body:JSON.stringify({urls:[url]})});
    if(!data.jobs?.length)throw Error("服务器未返回任务");
    let job=data.jobs[0];singleJobs.set(url,job);
    pendingSingle.delete(url);updateSingleButtons();notify("已开始单本下载");
    for(;;){
      if(job.state==="completed"){
        // Do not trigger a file download in a background tab. Always retain manual Save.
        if(!document.hidden)saveSingle(job);
        notify("TXT 已就绪，如未自动保存请点击「保存 TXT」");break;
      }
      if(!["queued","running","cancelling"].includes(job.state)){notify("下载未完成："+(job.error||labels[job.state]||job.state));break;}
      await new Promise(resolve=>setTimeout(resolve,2500));
      const batch=await request("/api/batches/"+encodeURIComponent(data.id));
      job=batch.jobs.find(j=>j.id===job.id);
      if(!job)throw Error("任务不存在");
      singleJobs.set(url,job);updateSingleButtons();
    }
  }catch(err){
    // A polling failure must not create another server job automatically.
    const job=singleJobs.get(url);
    if(job&&["queued","running","cancelling"].includes(job.state)){
      notify("任务已提交，请在下载列表查看进度");
    }else notify("下载失败："+err.message);
  }finally{pendingSingle.delete(url);updateSingleButtons();await refresh();}
}
function setupNavigation(){
  const search=document.querySelector("#search-page"),queue=document.querySelector("#queue-page");
  if(!search||!queue)return;
  function navigate(){
    const isQueue=location.hash==="#queue";
    search.hidden=isQueue;queue.hidden=!isQueue;
    document.querySelector("#heading").textContent=isQueue?"下载列表":"聚合搜索";
    document.querySelectorAll("[data-page]").forEach(a=>{
      if(a.dataset.page===(isQueue?"queue":"search"))a.setAttribute("aria-current","page");
      else a.removeAttribute("aria-current");
    });
    if(isQueue)refresh();window.scrollTo(0,0);
  }
  window.addEventListener("hashchange",navigate);navigate();
}

const canSaveDirectory = typeof window.showDirectoryPicker === "function";

// Folder handles are kept only in this tab. The server remains the source of
// truth for queued jobs; completed files can be recovered with "save all".
const autoSaveBatches = new Map();
let saveQueue = Promise.resolve();
if (canSaveDirectory) {
  folderActions.hidden = false;
  manualSubmitButton.hidden = false;
  submitButton.textContent = "选择文件夹并开始下载";
  folderHint.textContent =
    "提交时选择一次文件夹，每本完成后自动保存；需要补存时可手动保存已完成的 TXT。";
} else {
  folderHint.textContent =
    "此浏览器不支持网页自动写入文件夹；任务完成后请逐本点击「下载 TXT」。";
}

function message(text, ok = false) {
  msg.textContent = text;
  msg.classList.toggle("ok", ok);
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (response.status === 401) {
    window.location.assign("/login?next=" + encodeURIComponent(window.location.pathname));
    throw new Error("登录已过期");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "HTTP " + response.status);
  }
  return response.json();
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function actionButton(text, endpoint, jobId) {
  const btn = el("button", "minor", text);
  btn.type = "button";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      await request("/api/jobs/" + encodeURIComponent(jobId) + "/" + endpoint, {
        method: "POST",
        body: "{}"
      });
      await refresh();
    } catch (err) {
      message("操作失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  });
  return btn;
}

const labels = {
  queued: "等待下载", running: "下载中", cancelling: "取消中",
  completed: "已完成", failed: "失败", partial: "部分章节失败",
  canceled: "已取消", expired: "文件已过期"
};

function render() {
  batchesNode.replaceChildren();
  if (!batches.length) {
    batchesNode.append(el("p", "muted", "暂无任务。"));
    return;
  }
  for (const batch of batches) {
    const group = el("div", "batch");
    const finish = batch.jobs.filter(j => j.state === "completed").length;
    group.append(el("div", "batch-title", "批次 " + batch.id.slice(0, 8) +
      " · " + finish + "/" + batch.jobs.length + " 本可下载"));
    const auto = autoSaveBatches.get(batch.id);
    for (const j of batch.jobs) {
      const row = el("div", "job");
      const detail = el("div", "job-main");
      detail.append(el("div", "job-title", j.title || j.url));
      let sub = labels[j.state] || j.state;
      if (j.total) sub += " · " + j.done + "/" + j.total + " 章";
      if (j.failed) sub += " · 失败 " + j.failed + " 章";
      if (auto && auto.saved.has(j.id)) sub += " · 已自动保存到设备";
      else if (auto && j.state === "completed" && auto.inFlight.has(j.id)) {
        sub += " · 正在保存到设备";
      }
      detail.append(el("div", "job-sub", sub));
      if (j.error) detail.append(el("div", "job-sub", j.error.slice(0, 200)));
      if (j.state === "running" && j.total) {
        const p = document.createElement("progress");
        p.value = j.done;
        p.max = j.total;
        detail.append(p);
      }
      const actions = el("div", "job-actions");
      if (j.state === "completed") {
        const link = el("a", "download", "下载 TXT");
        link.href = j.download_url;
        link.download = j.filename || "novel.txt";
        link.rel = "nofollow";
        actions.append(link);
      }
      if (["queued", "running"].includes(j.state)) {
        actions.append(actionButton("取消", "cancel", j.id));
      }
      if (["failed", "partial", "canceled", "expired"].includes(j.state)) {
        actions.append(actionButton("重试", "retry", j.id));
      }
      row.append(detail, actions);
      group.append(row);
    }
    batchesNode.append(group);
  }
}

async function findUnusedFilename(directory, filename) {
  const safe = (filename || "novel.txt")
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_")
    .trim() || "novel.txt";
  const dot = safe.lastIndexOf(".");
  const stem = dot > 0 ? safe.slice(0, dot) : safe;
  const ext = dot > 0 ? safe.slice(dot) : "";
  // Never overwrite a file already present in the selected directory.
  for (let index = 0; index < 1000; index += 1) {
    const name = index === 0 ? safe : stem + " (" + (index + 1) + ")" + ext;
    try {
      await directory.getFileHandle(name);
    } catch (err) {
      if (err.name === "NotFoundError") return name;
      // A directory with the same name also occupies that filename.
      if (err.name === "TypeMismatchError") continue;
      throw err;
    }
  }
  throw new Error("目标目录存在太多同名文件");
}

async function saveTxtToDirectory(directory, job) {
  const response = await fetch(job.download_url, {
    credentials: "same-origin",
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("下载 " + (job.title || job.url) + " 失败（HTTP " + response.status + "）");
  }
  const filename = await findUnusedFilename(directory, job.filename || job.id + ".txt");
  const file = await directory.getFileHandle(filename, { create: true });
  const writable = await file.createWritable();
  let finished = false;
  try {
    await writable.write(await response.blob());
    await writable.close();
    finished = true;
  } finally {
    if (!finished) await writable.abort().catch(() => {});
  }
  return filename;
}

async function saveAutomatically(session, job) {
  try {
    await saveTxtToDirectory(session.directory, job);
    session.saved.add(job.id);
    const total = session.jobIds.size;
    message("已自动保存 " + session.saved.size + "/" + total +
      " 本 TXT 到所选文件夹。", true);
  } catch (err) {
    if (["NotAllowedError", "SecurityError"].includes(err.name)) {
      session.paused = true;
      message("文件夹写入权限失效，请使用「补存已完成 TXT」重新选择文件夹。");
    } else {
      // Retry transient download/write failures without retrying every 4 seconds.
      session.retryAfter.set(job.id, Date.now() + 30000);
      message("自动保存失败：" + err.message + "；30 秒后重试，亦可手动下载。");
    }
  } finally {
    session.inFlight.delete(job.id);
    render();
  }
}

function enqueueAutoSaves() {
  const now = Date.now();
  for (const batch of batches) {
    const session = autoSaveBatches.get(batch.id);
    if (!session || session.paused) continue;
    for (const job of batch.jobs) {
      if (job.state !== "completed" || !session.jobIds.has(job.id) ||
          session.saved.has(job.id) || session.inFlight.has(job.id) ||
          (session.retryAfter.get(job.id) || 0) > now) continue;
      session.inFlight.add(job.id);
      // Serialize saves so filename collision checks do not race each other.
      saveQueue = saveQueue.then(() => saveAutomatically(session, job));
    }
  }
}

async function refresh() {
  try {
    const payload = await request("/api/batches");
    batches = payload.batches;
    for(const [url,job] of singleJobs){
      const latest=batches.flatMap(b=>b.jobs).find(j=>j.id===job.id);
      if(latest)singleJobs.set(url,latest);
    }
    updateSingleButtons();
    render();
    enqueueAutoSaves();
  } catch (err) {
    message("读取任务失败：" + err.message);
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const urls = links.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (!urls.length) return;
  const manual = event.submitter === manualSubmitButton;
  submitButton.disabled = true;
  manualSubmitButton.disabled = true;
  try {
    let directory = null;
    if (canSaveDirectory && !manual) {
      // Must be called directly from the submit gesture, before network awaits.
      try {
        directory = await window.showDirectoryPicker({
          id: "ks-novels-txt",
          mode: "readwrite"
        });
      } catch (err) {
        if (err.name === "AbortError") {
          message("已取消文件夹选择，未创建下载任务。");
        } else {
          message("无法取得文件夹写入权限：" + err.message +
            "。如需手动下载，可点击「仅加入队列」。");
        }
        return;
      }
    }
    const data = await request("/api/batches", {
      method: "POST",
      body: JSON.stringify({ urls })
    });
    if (directory) {
      autoSaveBatches.set(data.id, {
        directory,
        jobIds: new Set(data.jobs.map(job => job.id)),
        saved: new Set(),
        inFlight: new Set(),
        retryAfter: new Map(),
        paused: false
      });
    }
    message("已加入队列：" + data.jobs.length + " 本。" +
      (directory ? " 完成后会自动保存到所选文件夹，请保持页面打开。" :
        " 完成后请逐本点击「下载 TXT」。"), true);
    links.value = "";
    await refresh();
  } catch (err) {
    message("提交失败：" + err.message);
  } finally {
    submitButton.disabled = false;
    manualSubmitButton.disabled = false;
  }
});

document.querySelector("#refresh").addEventListener("click", refresh);
document.querySelector("#save-all").addEventListener("click", async () => {
  const ready = batches.flatMap(b => b.jobs).filter(j => j.state === "completed");
  if (!ready.length) {
    message("当前没有已完成的 TXT。");
    return;
  }
  try {
    const directory = await window.showDirectoryPicker({
      id: "ks-novels-txt",
      mode: "readwrite"
    });
    let count = 0;
    let failed = 0;
    for (const job of ready) {
      try {
        await saveTxtToDirectory(directory, job);
        count += 1;
        message("已补存 " + count + "/" + ready.length + " 本…", true);
      } catch (err) {
        failed += 1;
        message("补存失败：" + err.message);
      }
    }
    message("补存完成：" + count + " 本成功，" + failed +
      " 本失败；每本均为独立 TXT。", failed === 0);
  } catch (err) {
    if (err.name !== "AbortError") message("保存失败：" + err.message);
  }
});

refresh();
setInterval(refresh, 4000);

async function setupSourceKit() {
  const panel = document.querySelector("#sourcekit-panel");
  if (!panel) return;
  try {
    const info = await request("/api/sourcekit/sources");
    if (!info.enabled){document.querySelector("#search-fallback").hidden=false;return;}
    panel.hidden = false;
    const sources = document.querySelector("#search-sources");
    const results = document.querySelector("#search-results");
    const status = document.querySelector("#search-status");
    const searchButton = document.querySelector("#search-submit");
    for (const [id, name] of Object.entries(info.sites)) {
      const label = el("label", "source-option");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = id;
      input.checked = true;
      label.append(input, document.createTextNode(" " + name));
      sources.append(label);
    }
    const dialog=document.querySelector("#source-dialog");
    function sourceSummary(){
      const n=sources.querySelectorAll("input:checked").length,total=Object.keys(info.sites).length;
      document.querySelector("#source-summary").textContent=n===total?"全部 "+n+" 个":n+" 个";
      document.querySelector("#selected-count").textContent="已选 "+n+" / "+total;
      document.querySelector("#toggle-all").textContent=n===total?"取消全选":"全部选择";
    }
    sources.addEventListener("change",sourceSummary);
    document.querySelector("#toggle-all").onclick=()=>{
      const all=[...sources.querySelectorAll("input")],checked=all.every(i=>i.checked);
      all.forEach(i=>i.checked=!checked);sourceSummary();
    };
    document.querySelector("#open-sources").onclick=()=>dialog.showModal();
    document.querySelector("#source-close").onclick=document.querySelector("#source-done").onclick=()=>dialog.close();
    dialog.addEventListener("click",e=>{if(e.target===dialog)dialog.close();});
    sourceSummary();
    const activity=document.querySelector("#search-activity"),countNode=document.querySelector("#result-count");
    let activeKeyword="", seen=new Set(), totals={}, cursors={}, states={};
    const more=el("button","more","继续加载");
    more.type="button";more.hidden=true;
    results.after(more);
    async function perform(keyword,ids,requestedCursors,append) {
      searchButton.disabled=true;more.disabled=true;
      activity.textContent=append?"正在加载更多…":"搜索中…";
      if(!append) {
        results.replaceChildren();seen=new Set();totals={};cursors={};states={};countNode.textContent="0";
      }
      try {
        const data=await request("/api/sourcekit/search",{method:"POST",body:JSON.stringify({keyword,sites:ids,cursors:requestedCursors})});
        for(const [id,state] of Object.entries(data.statuses)) {
          states[id]=state;
          if(Number.isInteger(state.next_page)&&state.next_page>0)cursors[id]=state.next_page;
          else delete cursors[id];
        }
        for(const row of data.results) {
          const key=row.site+"|"+row.book_url;
          if(seen.has(key))continue;
          seen.add(key);totals[row.site]=(totals[row.site]||0)+1;
          const item=el("article","book"),fold=el("details"),summary=el("summary"),main=el("div","book-main");
          main.append(el("span","book-title",row.title||row.book_url));
          const meta=el("div","book-meta");
          meta.append(el("span","",(row.author||"未知作者")),el("span","source-note","· "+(info.sites[row.site]||row.site)));
          main.append(meta);summary.append(main,el("span","chevron","⌄"));
          const desc=el("div","desc"),description=el("div","desc-copy","展开后读取简介"),note=el("div","search-note"),detail=el("div","desc-actions");
          desc.append(description,note,detail);
          let loaded=false,loading=false;
          async function loadDescription() {
            if(loaded||loading)return;
            loading=true;description.textContent="读取简介…";note.replaceChildren();
            try {
              const data=await request("/api/sourcekit/detail?"+new URLSearchParams({site:row.site,url:row.book_url}));
              description.textContent=data.description||"原站暂无简介";
              note.textContent=data.kind==="excerpt"?"详情页简介（未能识别完整简介边界）":"";
              loaded=true;
            }catch(err){
              description.textContent="简介暂不可用："+err.message;
              const retry=el("button","minor","重试简介");retry.type="button";
              retry.addEventListener("click",loadDescription);note.append(retry);
            }finally{loading=false;}
          }
          let button;
          if(row.direct_url&&row.site==="sk_jiujiu") {
            button=el("a","book-action","下载 TXT");button.href=row.direct_url;
          } else {
            button=el("button","book-action","下载 TXT");button.type="button";
            button.dataset.singleUrl=row.book_url;button.addEventListener("click",()=>downloadSingle(row.book_url));
          }
          const original=el("a","text-link","原站详情 ↗");original.href=row.book_url;
          original.target="_blank";original.rel="noopener noreferrer";detail.append(original);
          fold.append(summary,desc);item.append(fold,button);
          fold.addEventListener("toggle",()=>{if(fold.open){detail.append(button);loadDescription();}else item.append(button);});
          results.append(item);
        }
        status.textContent=Object.entries(states).map(([id,state])=>{
          const name=info.sites[id]||id;
          const count=totals[id]||0;
          let tail=cursors[id]?" · 可继续加载":"";
          if(state.state==="catalog")tail+=" · 仅已同步书单";
          else if(state.state==="blocked")tail+=" · 原站要求浏览器验证";
          else if(state.state==="failed")tail+=" · 部分请求失败";
          else if(state.state!=="completed")tail+=" · 状态未知";
          return name+"：已获取 "+count+" 条"+tail;
        }).join("\n");
        countNode.textContent=seen.size;activity.textContent=Object.values(states).some(s=>["blocked","failed"].includes(s.state))?"部分书源异常":"";
        updateSingleButtons();
        if(data.statuses.sk_czbooks&&["blocked","failed"].includes(data.statuses.sk_czbooks.state)&&!results.querySelector("[data-cz-manual]")){
          const manual=el("a","manual-search","在小说狂人原站搜索");
          manual.dataset.czManual="1";manual.href="https://czbooks.net/s/"+encodeURIComponent(keyword);
          manual.target="_blank";manual.rel="noopener noreferrer";results.append(manual);
        }
      }catch(err){activity.textContent="搜索失败";status.textContent="搜索失败："+err.message+(append?"（已保留此前结果，可重试）":"");document.querySelector(".source-report").open=true;}
      finally{more.hidden=Object.keys(cursors).length===0;searchButton.disabled=false;more.disabled=false;}
    }
    document.querySelector("#sourcekit-search").addEventListener("submit",async event=>{
      event.preventDefault();
      const keyword=document.querySelector("#search-keyword").value.trim();
      const ids=[...sources.querySelectorAll("input:checked")].map(input=>input.value);
      if(!keyword||!ids.length){activity.textContent="请选择书源并输入关键词。";return;}
      activeKeyword=keyword;await perform(keyword,ids,{},false);
    });
    more.addEventListener("click",()=>perform(activeKeyword,Object.keys(cursors),{...cursors},true));
  } catch (_) { const fallback=document.querySelector("#search-fallback");if(fallback)fallback.hidden=false; }
}
setupNavigation();
setupSourceKit();
