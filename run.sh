#!/usr/bin/env bash
# Start the SharePoint Video Downloader (runs the one-time setup first if needed).
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 setup.py
exec .venv/bin/python app.py
