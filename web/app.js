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
    if (!info.enabled) return;
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
    let activeKeyword="", seen=new Set(), totals={}, cursors={}, states={};
    const more=el("button","minor","继续加载");
    more.type="button";more.hidden=true;
    results.after(more);
    async function perform(keyword,ids,requestedCursors,append) {
      searchButton.disabled=true;more.disabled=true;
      status.textContent=append?"正在加载更多…":"搜索中…";
      if(!append) {
        results.replaceChildren();seen=new Set();totals={};cursors={};states={};
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
          const item=el("details","search-book");
          const summary=el("summary");
          summary.append(el("span","search-title",row.title||row.book_url),el("span","search-meta",(row.author||"未知作者")+" · "+(info.sites[row.site]||row.site)));
          const description=el("div","search-description","展开后读取简介");
          const note=el("div","muted small");
          const detail=el("div","actions search-actions");
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
          item.addEventListener("toggle",()=>{if(item.open)loadDescription();});
          item.append(summary,description,note,detail);
          if(row.direct_url&&row.site==="sk_jiujiu") {
            const direct=el("a","download","原站 TXT");direct.href=row.direct_url;detail.append(direct);
          } else {
            const button=el("button","minor","加入列表");
            button.type="button";
            button.addEventListener("click",()=>{
              const existing=links.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
              if(!existing.includes(row.book_url))existing.push(row.book_url);
              links.value=existing.join("\n");button.textContent="已加入";
            });
            detail.append(button);
          }
          const original=el("a","search-original","原站详情");original.href=row.book_url;
          original.target="_blank";original.rel="noopener noreferrer";detail.append(original);
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
        }).join(" | ");
        if(data.statuses.sk_czbooks&&["blocked","failed"].includes(data.statuses.sk_czbooks.state)&&!results.querySelector("[data-cz-manual]")){
          const manual=el("a","download","在小说狂人原站搜索");
          manual.dataset.czManual="1";manual.href="https://czbooks.net/s/"+encodeURIComponent(keyword);
          manual.target="_blank";manual.rel="noopener noreferrer";results.append(manual);
        }
      }catch(err){status.textContent="搜索失败："+err.message+(append?"（已保留此前结果，可重试）":"");}
      finally{more.hidden=Object.keys(cursors).length===0;searchButton.disabled=false;more.disabled=false;}
    }
    document.querySelector("#sourcekit-search").addEventListener("submit",async event=>{
      event.preventDefault();
      const keyword=document.querySelector("#search-keyword").value.trim();
      const ids=[...sources.querySelectorAll("input:checked")].map(input=>input.value);
      if(!keyword||!ids.length){status.textContent="请选择书源并输入关键词。";return;}
      activeKeyword=keyword;await perform(keyword,ids,{},false);
    });
    more.addEventListener("click",()=>perform(activeKeyword,Object.keys(cursors),{...cursors},true));
  } catch (_) { /* Keep the original 52 download page available. */ }
}
setupSourceKit();
