# ks-novels

Personal 52书库 to UTF-8 TXT browser service. Frontend and backend are
maintained together, separately from the NixOS fleet deployment.

## Features

- Paste 1–30 52书库 book-homepage URLs, one per line.
- Persistent SQLite queue with a single YuriScraper worker, status polling,
  cancel/retry and 12-hour temporary TXT storage.
- Each completed book is a separate TXT attachment downloaded directly by the
  browser to its own device; no ZIP output.
- iPhone Chrome and all other browsers support individual TXT links. Android
  and desktop Chrome additionally offer directory batch save when their version
  supports the File System Access API.
- HTTP Basic authentication using a runtime credential, without hardcoded
  passwords or published novel content.

## Layout

- web/app.py: FastAPI endpoints, queue, downloader subprocess, access control.
- web/app.js: Browser interactions, progress, local TXT save.
- web/index.html: Mobile-first page.
- web/style.css: Responsive styling.
- tests/: Regression tests.

## Development

Use Python 3.11+ and a local YuriScraper binary. For example:

    python -m venv .venv
    . .venv/bin/activate
    pip install -e '.[test]'
    pytest

Supply a 24+ character password in a private file and start locally:

    export NOVELS_PASSWORD_FILE=/path/to/private/password-file
    export NOVELS_DATA_DIR=/tmp/ks-novels-dev
    export NOVELS_YURI_BIN=/path/to/yuri-scraper
    export NOVELS_ORIGIN=http://127.0.0.1:18765
    uvicorn app:app --app-dir web --host 127.0.0.1 --port 18765

For remote use, require HTTPS and a secure reverse proxy. Default username for
the current private deployment is ks.

## nixos-config integration

The remote-source switch remains pending until this GitHub repository has been created and pushed. The live VPS is still using the in-repository web source.


The separate kezish/nixos-config repository remains the NixOS deployment owner:
- Deployment registry and host settings for raksmart-cn2.
- Caddy TLS routing, systemd service sandbox and SOPS admin password.
- YuriScraper pinned Go source and preserve-txt-introduction.patch.
- After migration, a pinned ks-novels source commit and hash fetched by Nix.

The production service uses the same SQLite and book-cache path
/var/lib/novels-web across updates, and passes NOVELS_YURI_BIN,
YURI_SCRAPER_CONFIG, NOVELS_DATA_DIR and NOVELS_ORIGIN via systemd.

Release sequence: commit app changes here, pin its hash in nixos-config,
run NixOS eval/build and remote test, verify NaiveProxy CONNECT behavior,
then merge and switch the host.

No novels, secrets, runtime caches, or generated database files in Git.
