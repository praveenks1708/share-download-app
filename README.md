# SharePoint Video Downloader

A small local web app for downloading videos from SharePoint / OneDrive links.

## Setup (one command)

Needs **Python 3.10+** (macOS's built-in 3.9 is too old: `brew install python`;
Windows: `winget install Python.Python.3.12`).

macOS:
```bash
git clone https://github.com/praveenks1708/share-download-app.git
cd share-download-app
python3 setup.py --os macos --run
```

Windows (PowerShell):
```powershell
git clone https://github.com/praveenks1708/share-download-app.git
cd share-download-app
py setup.py --os windows --run
```

What `setup.py` does (safe to re-run; anything already installed is skipped):

| Step | macOS (`--os macos`) | Windows (`--os windows`) |
|---|---|---|
| 1. ffmpeg, if missing | `brew install ffmpeg` | `winget install Gyan.FFmpeg` |
| 1. Google Chrome, if missing | `brew install --cask google-chrome` | `winget install Google.Chrome` |
| 2. Python packages | `.venv/` + `requirements.txt` | `.venv\` + `requirements.txt` |
| 3. Playwright browser | `playwright install chromium` | same |
| 4. Check ffmpeg | falls back to the copy bundled with `imageio-ffmpeg` | same |

Options: `--run` starts the app afterwards · `--no-system` installs Python packages only ·
`--no-chrome` skips Chrome · `--os` defaults to the OS it is run on, and must match it.
If Homebrew / winget is missing, setup continues using the bundled ffmpeg and Playwright's Chromium.

Start it later with `./run.sh` (macOS) or `.venv\Scripts\python app.py` (Windows); it opens
http://127.0.0.1:8765. Set `PORT=` to change the port.

## Using it

Pick the link type with the two buttons at the top, **Video links** or **Folder link**, then paste links
one per line. Optionally set a **Folder name** (created inside the base folder) and a **File name prefix**.
Videos are saved as `<prefix>_1.mp4`, `<prefix>_2.mp4`, … (or `1.mp4`, `2.mp4`, …) in download order,
continuing after the highest number already in that folder.

**Folder link** mode (a `/:f:/` sharing link, or a library / OneDrive folder URL): the app lists every
video in the folder, however many there are (listings are paged), and downloads them one by one, each
through the same original-file / view-only-stream logic. Non-video files are skipped.
- **Download order:** date modified oldest first (default), date modified newest first,
  date created, or name (natural: 1, 2, 10).
- **Include subfolders** to go deeper.

A folder link pasted in Video mode (or a video link in Folder mode) stops with a message telling you
to switch.

How it works:
1. Each link opens in a real Chrome window with its own saved profile (`.browser-profile/`).
   You sign in to Microsoft 365 there yourself the first time; the sign-in is kept for later runs.
2. The app finds the file path (sharing links redirect to `stream.aspx?id=...`) and downloads the
   **original file** through SharePoint's `download.aspx` using that browser session.
3. If downloads are turned off for the file (view-only share), it saves the **stream the web player uses**
   with yt-dlp + ffmpeg. It finds that stream two ways, trying each until one works:
   - builds the `videomanifest` URL from the page's `RenderListDataAsStream` response
     (`.videoManifestUrl` template + `.driveAccessToken`), the method kylon/Sharedown uses, so it
     works even if the player never starts;
   - otherwise grabs the `videomanifest` request the player makes.

Only download videos you have access to and are permitted to save under your organisation's policy.
