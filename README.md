# SharePoint Video Downloader

A small local web app for downloading videos from SharePoint / OneDrive links.

```bash
./run.sh        # first run sets up .venv + Playwright; then opens http://127.0.0.1:8765
```

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
Requires Python 3.10+ and ffmpeg (`brew install ffmpeg`). Set `PORT=` to change the port.
