"""Local web UI for downloading SharePoint / OneDrive videos.

Run:  ./run.sh   (or: .venv/bin/python app.py)   then open http://127.0.0.1:8765
"""
from __future__ import annotations

import os
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
    out_dir = (data.get("out_dir") or DEFAULT_OUT).strip()
    urls = [u.strip() for u in (data.get("urls") or "").splitlines() if u.strip()]
    bad = [u for u in urls if not u.lower().startswith("https://")]
    if not urls or bad:
        return jsonify(error="Paste one or more https:// SharePoint links, one per line."), 400
    created = [dl.submit(u, out_dir).id for u in urls]
    return jsonify(ok=True, ids=created)


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
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
    return jsonify(ok=True)


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"SharePoint Video Downloader running at {url}")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
