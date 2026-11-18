"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appScript = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");

class FakeElement {
  constructor() {
    this.listeners = {};
    this.children = [];
    this.classList = { toggle() {} };
    this.disabled = false;
    this.hidden = true;
    this.textContent = "";
    this.value = "";
    this.style = {};
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}

function setup({ picker = true, pickerError = null, existing = [] } = {}) {
  const nodes = Object.fromEntries(
    ["#submit-form", "#links", "#message", "#batches", "#folder-actions",
      "#save-hint", "#submit", "#queue-only", "#refresh", "#save-all"]
      .map(id => [id, new FakeElement()])
  );
  const trace = [];
  const fileNames = new Set(existing);
  const writes = [];
  const directory = {
    async getFileHandle(name, options) {
      if (!options || !options.create) {
        if (fileNames.has(name)) return {};
        const err = new Error("Missing file");
        err.name = "NotFoundError";
        throw err;
      }
      return {
        async createWritable() {
          let data;
          return {
            async write(bytes) { data = bytes; },
            async close() {
              writes.push({ name, data });
              fileNames.add(name);
            },
            async abort() {}
          };
        }
      };
    }
  };
  const jobs = [
    { id: "j1", title: "Book1", state: "queued", filename: "Book1.txt",
      download_url: "/api/jobs/j1/file", done: 0, total: 0, failed: 0 },
    { id: "j2", title: "Book2", state: "queued", filename: "Book2.txt",
      download_url: "/api/jobs/j2/file", done: 0, total: 0, failed: 0 }
  ];
  let submitted = false;
  let fileFetches = 0;
  const fetch = async (url, options = {}) => {
    if (url === "/api/batches" && options.method === "POST") {
      trace.push("post");
      submitted = true;
      return {
        ok: true,
        json: async () => ({ id: "batch-1", jobs: jobs.map(job => ({ id: job.id })) })
      };
    }
    if (url === "/api/batches") {
      return {
        ok: true,
        json: async () => ({ batches: submitted ? [{ id: "batch-1", jobs }] : [] })
      };
    }
    if (url.includes("/file")) {
      fileFetches += 1;
      return { ok: true, blob: async () => "TXT content for " + url };
    }
    throw new Error("Unknown URL: " + url);
  };
  const window = {};
  if (picker) {
    window.showDirectoryPicker = async () => {
      trace.push("picker");
      if (pickerError) throw pickerError;
      return directory;
    };
  }
  const context = vm.createContext({
    window,
    document: {
      querySelector: id => nodes[id],
      createElement: () => new FakeElement()
    },
    fetch,
    setInterval() {},
    Date,
    Set,
    Map
  });
  vm.runInContext(appScript, context, { filename: "web/app.js" });

  async function submit(manual = false) {
    const button = nodes[manual ? "#queue-only" : "#submit"];
    nodes["#links"].value =
      "https://www.52shuku.net/yanqing/17_b/bkfXm.html\n" +
      "https://www.52shuku.net/yanqing/17_b/abc.html";
    await nodes["#submit-form"].listeners.submit({
      preventDefault() {},
      submitter: button
    });
    await vm.runInContext("saveQueue", context);
  }
  async function refresh() {
    await nodes["#refresh"].listeners.click();
    await vm.runInContext("saveQueue", context);
  }
  return {
    nodes, trace, jobs, writes, submit, refresh,
    submitted: () => submitted,
    fileFetches: () => fileFetches
  };
}

test("selects directory before submitting and auto-saves each completed book once", async () => {
  const app = setup({ existing: ["Book1.txt"] });
  assert.equal(app.nodes["#submit"].textContent, "选择文件夹并开始下载");
  assert.equal(app.nodes["#queue-only"].hidden, false);

  await app.submit();
  assert.ok(app.trace.indexOf("picker") < app.trace.indexOf("post"));
  assert.equal(app.writes.length, 0);

  app.jobs[0].state = "completed";
  await app.refresh();
  assert.equal(app.writes.length, 1);
  assert.equal(app.writes[0].name, "Book1 (2).txt");

  app.jobs[1].state = "completed";
  await app.refresh();
  assert.equal(app.writes.length, 2);
  assert.equal(app.writes[1].name, "Book2.txt");

  await app.refresh();
  assert.equal(app.writes.length, 2);
  assert.equal(app.fileFetches(), 2);
  assert.match(app.nodes["#message"].textContent, /已自动保存 2\/2/);
});

test("manual-only submission avoids directory picker and automatic file fetches", async () => {
  const app = setup();
  await app.submit(true);
  assert.deepEqual(app.trace, ["post"]);
  app.jobs[0].state = "completed";
  await app.refresh();
  assert.equal(app.fileFetches(), 0);
  assert.match(app.nodes["#message"].textContent, /逐本点击/);
});

test("iOS-style browser without folder picker keeps individual downloads", async () => {
  const app = setup({ picker: false });
  assert.equal(app.nodes["#folder-actions"].hidden, true);
  assert.equal(app.nodes["#queue-only"].hidden, true);
  await app.submit();
  assert.deepEqual(app.trace, ["post"]);
  app.jobs[0].state = "completed";
  await app.refresh();
  assert.equal(app.writes.length, 0);
});

test("canceling directory selection never submits the batch", async () => {
  const error = new Error("User canceled");
  error.name = "AbortError";
  const app = setup({ pickerError: error });
  await app.submit();
  assert.equal(app.submitted(), false);
  assert.deepEqual(app.trace, ["picker"]);
  assert.match(app.nodes["#message"].textContent, /未创建下载任务/);
});
