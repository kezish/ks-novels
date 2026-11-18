"use strict";
const form = document.querySelector("#submit-form");
const links = document.querySelector("#links");
const msg = document.querySelector("#message");
const batchesNode = document.querySelector("#batches");
const folderActions = document.querySelector("#folder-actions");
const folderHint = document.querySelector("#save-hint");
let batches = [];
const canSaveDirectory = typeof window.showDirectoryPicker === "function";
if (canSaveDirectory) {
  folderActions.hidden = false;
  folderHint.textContent = "每本 TXT 可单独下载；也可选择一次文件夹，保存全部已完成的 TXT。";
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
    } finally { btn.disabled = false; }
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
    for (const j of batch.jobs) {
      const row = el("div", "job");
      const detail = el("div", "job-main");
      detail.append(el("div", "job-title", j.title || j.url));
      let sub = labels[j.state] || j.state;
      if (j.total) sub += " · " + j.done + "/" + j.total + " 章";
      if (j.failed) sub += " · 失败 " + j.failed + " 章";
      detail.append(el("div", "job-sub", sub));
      if (j.error) detail.append(el("div", "job-sub", j.error.slice(0, 200)));
      if (j.state === "running" && j.total) {
        const p = document.createElement("progress");
        p.value = j.done; p.max = j.total;
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
async function refresh() {
  try {
    const payload = await request("/api/batches");
    batches = payload.batches;
    render();
  } catch (err) {
    message("读取任务失败：" + err.message);
  }
}
form.addEventListener("submit", async event => {
  event.preventDefault();
  const urls = links.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (!urls.length) return;
  document.querySelector("#submit").disabled = true;
  try {
    const data = await request("/api/batches", {
      method: "POST", body: JSON.stringify({ urls })
    });
    message("已加入队列：" + data.jobs.length + " 本。", true);
    links.value = "";
    await refresh();
  } catch (err) {
    message("提交失败：" + err.message);
  } finally { document.querySelector("#submit").disabled = false; }
});
document.querySelector("#refresh").addEventListener("click", refresh);
document.querySelector("#save-all").addEventListener("click", async () => {
  const ready = batches.flatMap(b => b.jobs).filter(j => j.state === "completed");
  if (!ready.length) {
    message("当前没有已完成的 TXT。");
    return;
  }
  try {
    // User interaction is required. Feature detection hides this on iPhone Chrome.
    const dir = await window.showDirectoryPicker({ mode: "readwrite" });
    let count = 0;
    for (const job of ready) {
      const res = await fetch(job.download_url, { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) throw new Error("下载 " + (job.title || job.url) + " 失败");
      const name = (job.filename || job.id + ".txt").replace(/[\\/]/g, "_");
      const file = await dir.getFileHandle(name, { create: true });
      const writable = await file.createWritable();
      try {
        await writable.write(await res.blob());
      } finally { await writable.close(); }
      count += 1;
      message("已保存 " + count + "/" + ready.length + " 本…", true);
    }
    message("已向你选择的目录保存 " + count + " 个独立 TXT。", true);
  } catch (err) {
    if (err.name !== "AbortError") message("保存失败：" + err.message);
  }
});
refresh();
setInterval(refresh, 4000);
