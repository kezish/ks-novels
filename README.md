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
  browser device. No ZIP files. On iPhone Chrome, tap each TXT download button;
  on Android and desktop Chrome, saving multiple separate TXT files into a
  selected directory is offered when the browser supports the API.
- One password-protected user: HTTP Basic auth over HTTPS in production.

## Local development

Use Go 1.26.5+ and Python 3.11+.

```bash
(cd scraper && CGO_ENABLED=0 go test -short ./...)
python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
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
