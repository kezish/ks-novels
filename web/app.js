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
