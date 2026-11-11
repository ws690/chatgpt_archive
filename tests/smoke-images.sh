#!/bin/sh
# Build first. These checks read no ChatGPT history and do not run the importer.
set -eu
cd "$(dirname "$0")/.."
./nas.sh config --quiet
./nas.sh run --rm --no-deps collector python /collector/collector.py --help
./nas.sh run --rm --no-deps collector python -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(chromium_sandbox=True); print("Sandboxed Chromium", b.version); b.close(); p.stop()'
./nas.sh run --rm --no-deps chatgpt-backup python -c 'import scheduler, backup; print("Backup application imports passed")'
