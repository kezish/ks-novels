# ks-novels

Independent, self-maintained novel downloader: a Go download engine, a
FastAPI job queue and a mobile-friendly browser UI. Initial support is for
52书库 with independent UTF-8 TXT downloads. Project language: Chinese.

## Repository map

| Directory | Owner | Purpose |
| --- | --- | --- |
| `scraper/` | Go | Full YuriScraper v0.7.0-derived download engine, site adapters, TXT/EPUB converter, command line and tests |
| `server/` | Python | FastAPI Basic auth, SQLite queue, worker, progress, retention, attachments |
| `web/` | HTML/CSS/JS | Responsive browser UI, progress polling and device-local TXT downloads |
| `tests/` | Python | API, authentication, URL safety and TXT attachment integration tests |
| `.github/workflows/` | CI | Go and Python regression tests |

## Features

- Paste 1–30 52书库 book-homepage URLs, one per line; pending queue limit 100.
- Persistent SQLite worker: download one book at a time (4 chapter workers,
  2 requests/s), retry/cancel tasks, temporary server files kept for 12 h.
- TXT preserves all front matter before chapter 1, including the synopsis,
  mini-theater, genre/tags and author's intention. The original chapter reader
  still strips duplicated intro from body content.
- Each book is delivered as an individual TXT attachment to the requesting
  browser device. No ZIP files. On desktop Chrome and supported Android Chrome,
  the submit button immediately asks for one destination folder and then
  automatically saves each completed TXT to that folder. Keep the tab open;
  an existing file is never overwritten (a numbered suffix is added).
  Browsers without File System Access directory support, including iPhone
  Chrome, keep per-book TXT download links. A manual queue-only button and
  save-completed-files recovery action remain available.
- One password-protected user: HTTP Basic auth over HTTPS in production.

## Local development

Use Go 1.26.5+ and Python 3.11+.

```bash
(cd scraper && CGO_ENABLED=0 go test -short ./...)
python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
node --test tests/test_browser_logic.cjs  # Node.js 22+, front-end flow tests
```

To run the application, create a private password file with a 24+ character
password and provide a working Go CLI:

```bash
(cd scraper && CGO_ENABLED=0 go build -o ../yuri-scraper ./cmd/yuri_scraper)
export NOVELS_PASSWORD_FILE=/path/to/private/password-file
export NOVELS_DATA_DIR=/tmp/ks-novels-dev
export NOVELS_YURI_BIN="$PWD/yuri-scraper"
export NOVELS_ORIGIN=http://127.0.0.1:18765
uvicorn app:app --app-dir server --host 127.0.0.1 --port 18765
```

HTTP Basic username for the existing private deployment is `ks`.
Localhost HTTP is for local testing only; public deployment uses Caddy HTTPS.
No user secrets, downloaded books, SQLite state, or runtime caches are tracked.

## Source and attribution

The scraper was initially copied from
[Eurekaimer/yuri_scraper](https://github.com/Eurekaimer/yuri_scraper) at
commit `4771d7ef22ee1c2116beecccc9636f9d222e798c` (v0.7.0), with the
existing tested full-introduction TXT patch incorporated directly into the
source. Go module/import paths have been relocated to
`github.com/kezish/ks-novels/scraper`. The original README and source
headers were preserved. See [scraper/UPSTREAM.md](scraper/UPSTREAM.md)
for provenance and the upstream project's MIT license declaration.

This repository is developed independently; periodic upstream merging is
not required. Keep attribution intact when modifying the derived source.

## NixOS deployment

`kezish/nixos-config` owns deployment concerns only: fixed-source Nix
fetching, Go build, Python runtime, the systemd sandbox, SOPS credentials,
Caddy TLS and instance settings. It pins a specific `ks-novels` commit
and verifies its source hash; it does not automatically deploy new pushes.
It retains the original YuriScraper module for compatibility/rollback.

The live application uses `/var/lib/novels-web` for the SQLite queue and
transient book files, and receives `NOVELS_YURI_BIN`, `YURI_SCRAPER_CONFIG`,
`NOVELS_DATA_DIR`, and `NOVELS_ORIGIN` from systemd. Updating application
source must preserve that state directory.

Release path: test/commit here → update pinned commit/hash in
`nixos-config` → Nix eval/build → remote `nixos-rebuild test` → verify
Caddy and NaiveProxy → merge → `nixos-rebuild switch`.

## 每日新书（双书源）

访问 /daily 查看每日书单，展开简介后下载。久久原站下载页在点击时解析
download_1 原生 TXT 链接并直接跳转，不要求预置登录 Cookie。52书库通过既有
Go 队列按需生成单本 TXT，完成后保存或手动点击保存。原批量下载入口保持不变。

- 久久：女生大类最新上传与只看新书两个榜单。
- 52：言情栏目（含部分无CP）；第一阅读页仅提取第一章前的完整简介。
- SQLite catalog.sqlite3 独立于 jobs.sqlite3；按书源ID及书名/作者标准化去重，
  保留来源，保守模糊匹配并记录疑似重复。
- 采集窗口初始七天，成功后保留48小时重叠；失败不推进水位；每日书单按
  首次发现时间发布，已发布作品不会因新增来源重复列入。
- 通知仅有 NotificationProvider 及待发送 outbox，目前没有配置推送渠道。

运行（生产环境仅由 nixos-config 声明的 systemd timer 调度，默认每小时采集、08:00 UTC+8 发布；以下命令供开发测试）：

    NOVELS_DATA_DIR=/var/lib/novels-web PYTHONPATH=server python -m catalog.cli sync
    NOVELS_DATA_DIR=/var/lib/novels-web PYTHONPATH=server python -m catalog.cli daily

新增书源实现 server/sources/base.py 的 SourceAdapter 并注册于 registry.py。
原生下载及现有52队列下载已支持，其他生成型下载器需增加对应下载包装。


生产环境的服务、定时任务及部署配置统一维护于 kezish/nixos-config。应用只提供一次性采集与发布 CLI，不包含独立的调度器。
