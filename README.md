# SharePoint Video Downloader

A small local web app for downloading videos from SharePoint / OneDrive links.

## Setup (one command)

Needs **Python 3.10+** (macOS's built-in 3.9 is too old: `brew install python`) and, ideally, Google Chrome.

```bash
git clone https://github.com/praveenks1708/share-download-app.git
cd share-download-app
python3 setup.py --run        # on Windows: py setup.py --run
```

`setup.py` installs everything inside this folder, and is safe to re-run:
- a virtual environment in `.venv/`
- the Python packages in `requirements.txt` (Flask, Playwright, yt-dlp, requests, imageio-ffmpeg)
- the Chromium browser Playwright uses
- ffmpeg: a bundled copy comes with `imageio-ffmpeg`; a system ffmpeg is used instead if one is installed

`--run` starts the app afterwards. Later, start it with `./run.sh` (macOS/Linux) or
`.venv\Scripts\python app.py` (Windows); it opens http://127.0.0.1:8765. Set `PORT=` to change the port.

## Using it

Paste links, optionally set a **Folder name** (created inside the base folder) and a **File name prefix**.
Videos are saved as `<prefix>_1.mp4`, `<prefix>_2.mp4`, … (or `1.mp4`, `2.mp4`, …), continuing after the
highest number already in that folder.

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
