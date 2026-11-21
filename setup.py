#!/usr/bin/env python3
"""One-command setup for SharePoint Video Downloader.

    python3 setup.py --os macos [--run]
    py setup.py --os windows [--run]

Steps:
  1. System tools via the OS package manager, only if missing:
       macos   -> Homebrew:  ffmpeg, Google Chrome
       windows -> winget:    ffmpeg (Gyan.FFmpeg), Google Chrome
  2. Virtual environment in .venv/ and the Python packages in requirements.txt
  3. Chromium for Playwright
  4. Verify ffmpeg (falls back to the copy bundled with imageio-ffmpeg)

Safe to re-run: anything already installed is skipped.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"

# Per-OS system tools: (name, how to detect it, install command)
CHROME_PATHS = {
    "macos": ["/Applications/Google Chrome.app"],
    "windows": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ],
}
TOOLS = {
    "macos": {
        "manager": "brew",
        "manager_help": "Install Homebrew from https://brew.sh, then re-run this setup.",
        "ffmpeg": ["brew", "install", "ffmpeg"],
        "chrome": ["brew", "install", "--cask", "google-chrome"],
    },
    "windows": {
        "manager": "winget",
        "manager_help": "winget comes with 'App Installer' from the Microsoft Store; install it, "
                        "then re-run this setup.",
        "ffmpeg": ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                   "--accept-source-agreements", "--accept-package-agreements"],
        "chrome": ["winget", "install", "--id", "Google.Chrome", "-e",
                   "--accept-source-agreements", "--accept-package-agreements"],
    },
}


def current_os() -> str:
    return {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), platform.system().lower())


def step(msg: str):
    print(f"\n==> {msg}", flush=True)


def run(cmd, **kw):
    print("    $ " + " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=HERE, **kw)


def venv_python(os_name: str) -> Path:
    return VENV / ("Scripts/python.exe" if os_name == "windows" else "bin/python")


def has_chrome(os_name: str) -> bool:
    return any(Path(os.path.expandvars(p)).exists() for p in CHROME_PATHS[os_name])


def install_system_tools(os_name: str, skip_chrome: bool) -> None:
    t = TOOLS[os_name]
    need = []
    if shutil.which("ffmpeg"):
        print(f"    ffmpeg: already installed ({shutil.which('ffmpeg')})")
    else:
        need.append("ffmpeg")
    if skip_chrome:
        print("    Google Chrome: skipped (--no-chrome); Playwright's Chromium will be used")
    elif has_chrome(os_name):
        print("    Google Chrome: already installed")
    else:
        need.append("chrome")
    if not need:
        return
    if not shutil.which(t["manager"]):
        print(f"    {t['manager']} not found, so {', '.join(need)} can't be installed automatically.")
        print(f"    {t['manager_help']}")
        print("    Continuing: the bundled ffmpeg and Playwright's Chromium will be used instead.")
        return
    for tool in need:
        try:
            run(t[tool])
        except subprocess.CalledProcessError:
            print(f"    Could not install {tool}; continuing with the bundled fallback.")


def main() -> int:
    detected = current_os()
    ap = argparse.ArgumentParser(description="Set up SharePoint Video Downloader.")
    ap.add_argument("--os", choices=sorted(TOOLS), default=detected if detected in TOOLS else None,
                    help=f"target operating system (default: detected, here '{detected}')")
    ap.add_argument("--run", action="store_true", help="start the app when setup finishes")
    ap.add_argument("--no-system", action="store_true",
                    help="skip installing system tools (ffmpeg, Chrome); Python packages only")
    ap.add_argument("--no-chrome", action="store_true", help="don't install Google Chrome")
    args = ap.parse_args()

    if args.os is None:
        ap.error(f"unsupported OS '{detected}'; pass --os macos or --os windows")
    if args.os != detected:
        ap.error(f"--os {args.os} was given but this computer is running {detected}. "
                 f"Run the setup on the {args.os} machine itself.")
    if sys.version_info < (3, 10):
        hint = "brew install python" if args.os == "macos" else "winget install Python.Python.3.12"
        print(f"Python 3.10+ is required (this is {sys.version.split()[0]}). Install it with: {hint}")
        return 1

    print(f"Setting up for {args.os} in {HERE}")

    step("1/4  System tools")
    if args.no_system:
        print("    skipped (--no-system)")
    else:
        install_system_tools(args.os, args.no_chrome)

    py = venv_python(args.os)
    step("2/4  Python packages")
    if not py.exists():
        print(f"    creating virtual environment in {VENV.name}/")
        venv.EnvBuilder(with_pip=True).create(VENV)
    pip = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    run(pip + ["--upgrade", "pip"])
    run(pip + ["-r", "requirements.txt"])

    step("3/4  Chromium for Playwright")
    run([py, "-m", "playwright", "install", "chromium"])

    step("4/4  Checking ffmpeg")
    out = subprocess.run([str(py), "-c", "from downloader import ffmpeg_path; print(ffmpeg_path() or '')"],
                         cwd=HERE, capture_output=True, text=True).stdout.strip()
    if not out:
        print("    ffmpeg not found; view-only videos will not download.")
        return 1
    print(f"    using {out}")
    if args.os == "windows" and "imageio_ffmpeg" in out and not args.no_system:
        print("    (if ffmpeg was just installed by winget, it is used from the next new terminal)")

    start = r".venv\Scripts\python app.py" if args.os == "windows" else "./run.sh"
    print(f"\nSetup complete. Start the app any time with:  {start}")

    if args.run:
        print()
        if args.os == "windows":
            return subprocess.call([str(py), "app.py"], cwd=HERE)
        os.chdir(HERE)
        os.execv(str(py), [str(py), "app.py"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"\nSetup failed while running: {' '.join(map(str, e.cmd))}")
        sys.exit(e.returncode or 1)
