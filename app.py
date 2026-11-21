"""Local web UI for downloading SharePoint / OneDrive videos.

Run:  ./run.sh   (or: .venv/bin/python app.py)   then open http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from downloader import Downloader

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = str(Path.home() / "Downloads" / "SharePoint Videos")
PORT = int(os.environ.get("PORT", "8765"))

app = Flask(__name__, static_folder=None)
dl = Downloader()


@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.get("/api/jobs")
def jobs():
    return jsonify(jobs=dl.list(), default_out=DEFAULT_OUT)


@app.post("/api/download")
def download():
    data = request.get_json(force=True)
    out_dir = _target_dir(data)
    prefix = _clean_name(data.get("prefix")).rstrip("_")
    urls = [u.strip() for u in (data.get("urls") or "").splitlines() if u.strip()]
    bad = [u for u in urls if not u.lower().startswith("https://")]
    if not urls or bad:
        return jsonify(error="Paste one or more https:// SharePoint links, one per line."), 400
    sort = data.get("sort") if data.get("sort") in ("modified", "modified_desc", "created", "name") \
        else "modified"
    mode = "folder" if data.get("mode") == "folder" else "video"
    jobs = dl.submit_batch(urls, out_dir, prefix, recursive=bool(data.get("recursive")), sort=sort,
                           mode=mode)
    return jsonify(ok=True, ids=[j.id for j in jobs], out_dir=out_dir)


def _target_dir(data: dict) -> str:
    """Base folder + optional folder name, as entered in the UI."""
    base = (data.get("out_dir") or DEFAULT_OUT).strip()
    folder = _clean_name(data.get("folder"))
    return str(Path(base).expanduser() / folder) if folder else str(Path(base).expanduser())


@app.post("/api/logs/clear")
def clear_logs():
    out_dir = _target_dir(request.get_json(force=True) or {})
    n = dl.clear_logs(out_dir)
    return jsonify(ok=True, removed=n, logs=str(Path(out_dir) / "logs"))


@app.post("/api/logs/open")
def open_logs():
    out_dir = _target_dir(request.get_json(force=True) or {})
    log = Path(out_dir) / "logs" / "downloader.log"
    if not log.exists():
        return jsonify(error=f"No logs yet in {log.parent}"), 404
    _reveal(log)
    return jsonify(ok=True, path=str(log))


def _reveal(p: Path):
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])


def _clean_name(s) -> str:
    """A single safe path component: no slashes, no leading dots."""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(s or "")).strip(" .")
    return s[:100]


@app.post("/api/signin")
def signin():
    url = (request.get_json(silent=True) or {}).get("url")
    dl.open_signin(url if url and url.startswith("https://") else None)
    return jsonify(ok=True)


@app.post("/api/clear")
def clear():
    dl.clear_finished()
    return jsonify(ok=True)


@app.post("/api/reveal")
def reveal():
    path = (request.get_json(force=True) or {}).get("path", "")
    p = Path(path).expanduser()
    if not p.exists():
        return jsonify(error="File not found"), 404
    _reveal(p)
    return jsonify(ok=True)


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"SharePoint Video Downloader running at {url}")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
