#!/usr/bin/env bash
# Start the SharePoint Video Downloader (creates its own virtualenv on first run).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (takes a minute)…"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/python -m playwright install chromium
fi
command -v ffmpeg >/dev/null || echo "Note: ffmpeg not found; install it (brew install ffmpeg) for stream fallback downloads."
exec .venv/bin/python app.py
