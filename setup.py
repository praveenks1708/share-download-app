#!/usr/bin/env python3
"""One-command setup for SharePoint Video Downloader (macOS, Windows, Linux).

    python3 setup.py          # install everything
    python3 setup.py --run    # install everything, then start the app

Installs, inside this folder only:
  1. a virtual environment (.venv)
  2. the Python packages in requirements.txt (Flask, Playwright, yt-dlp,
     requests, imageio-ffmpeg - which ships its own ffmpeg binary)
  3. the Chromium browser Playwright uses
Safe to run again: it only updates what is missing or out of date.
"""
from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def step(msg: str):
    print(f"\n==> {msg}", flush=True)


def run(*args: str):
    subprocess.run([str(a) for a in args], check=True, cwd=HERE)


def main() -> int:
    if sys.version_info < (3, 10):
        print(f"Python 3.10+ is required (you have {sys.version.split()[0]}).")
        return 1

    if not PY.exists():
        step(f"Creating virtual environment in {VENV.name}/")
        venv.EnvBuilder(with_pip=True).create(VENV)

    step("Installing Python packages from requirements.txt")
    run(PY, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip")
    run(PY, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements.txt")

    step("Installing the Chromium browser for Playwright")
    run(PY, "-m", "playwright", "install", "chromium")

    step("Checking ffmpeg")
    out = subprocess.run(
        [str(PY), "-c", "from downloader import ffmpeg_path; print(ffmpeg_path() or '')"],
        cwd=HERE, capture_output=True, text=True,
    ).stdout.strip()
    if not out:
        print("ffmpeg could not be found or unpacked; view-only videos will not download.")
        return 1
    print(f"Using ffmpeg: {out}")

    start = (r".venv\Scripts\python app.py" if os.name == "nt" else ".venv/bin/python app.py")
    print(f"\nSetup complete. Start the app with:  {start}"
          + ("" if os.name == "nt" else "   (or ./run.sh)"))

    if "--run" in sys.argv:
        os.chdir(HERE)
        os.execv(str(PY), [str(PY), "app.py"]) if os.name != "nt" else run(PY, "app.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"\nSetup failed while running: {' '.join(map(str, e.cmd))}")
        sys.exit(e.returncode or 1)
