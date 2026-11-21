"""SharePoint video download engine.

All Playwright work happens on one worker thread (Playwright's sync API is not
thread-safe). Jobs are queued from the web UI and processed one at a time.

Strategy per URL:
  1. Open the URL in a real (headed) browser with a persistent profile, so the
     user's Microsoft sign-in is reused. If a login page appears, wait for the
     user to sign in in that window.
  2. Work out the file's server-relative path (the `id=` of stream.aspx, which
     sharing links redirect to) and try SharePoint's own download endpoint with
     the browser's cookies -> original file, full quality.
  3. If downloading is blocked (view-only share), fall back to the DASH
     `videomanifest` the web player requests and fetch it with yt-dlp/ffmpeg.
"""
from __future__ import annotations

import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

import requests

HERE = Path(__file__).resolve().parent
PROFILE_DIR = HERE / ".browser-profile"
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv")
LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "login.microsoft.com")
SIGN_IN_TIMEOUT = 600  # seconds to wait for the user to finish signing in
MANIFEST_TIMEOUT = 45  # seconds to wait for the player to request its manifest
# SharePoint list-data calls whose JSON carries the .videoManifestUrl template.
LIST_DATA_CALLS = ("RenderListDataAsStream", "GetListDataAsStream")


@dataclass
class Job:
    url: str
    out_dir: str
    prefix: str = ""  # file names become <prefix>_N (or N)
    name: str = ""  # assigned when the download starts, e.g. "class_3"
    kind: str = "video"  # video | folder
    parent: str = ""  # folder job this video came from
    source: str = ""  # original file name on SharePoint
    recursive: bool = False  # folder links: include subfolders
    sort: str = "name"  # folder links: name | created | modified
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "queued"  # queued | running | waiting_login | done | error
    message: str = "Waiting in queue"
    progress: float = 0.0  # 0..1, or -1 when unknown
    filename: str = ""
    path: str = ""
    method: str = ""
    created: float = field(default_factory=time.time)

    def update(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class Downloader:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._pw = None
        self._ctx = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ---------- public API (any thread) ----------
    def submit_batch(self, urls: list[str], out_dir: str, prefix: str = "",
                     recursive: bool = False, sort: str = "name") -> list[Job]:
        """Queue video or folder links. Files are named <prefix>_N (or N) when
        each download starts, continuing after the highest number in that folder,
        so numbers follow download order (folder contents included)."""
        jobs = [Job(url=u.strip(), out_dir=out_dir, prefix=prefix, recursive=recursive, sort=sort)
                for u in urls]
        with self._lock:
            for job in jobs:
                self.jobs[job.id] = job
                self.order.append(job.id)
        for job in jobs:
            self._q.put(("job", job))
        return jobs

    def _assign_name(self, job: Job) -> str:
        with self._lock:
            n = self._next_number(job.out_dir, job.prefix)
            job.name = f"{job.prefix}_{n}" if job.prefix else str(n)
        return job.name

    def _next_number(self, out_dir: str, prefix: str) -> int:
        pat = re.compile(rf"^{re.escape(prefix + '_') if prefix else ''}(\d+)(\.[^.]+)*$", re.I)
        used = []
        d = Path(out_dir).expanduser()
        if d.is_dir():
            used += [int(m.group(1)) for f in d.iterdir() if (m := pat.match(f.name))]
        # numbers taken by downloads still running for the same folder
        used += [int(m.group(1)) for j in self.jobs.values()
                 if j.out_dir == out_dir and j.status not in ("done", "error")
                 and (m := pat.match(j.name))]
        return max(used, default=0) + 1

    def open_signin(self, url: str | None):
        self._q.put(("signin", url or "https://www.office.com/"))

    def list(self):
        return [asdict(self.jobs[i]) for i in self.order]  # download order

    def clear_finished(self):
        self.order = [i for i in self.order if self.jobs[i].status not in ("done", "error")]

    # ---------- worker thread ----------
    def _loop(self):
        while True:
            kind, arg = self._q.get()
            try:
                if kind == "signin":
                    page = self._context().new_page()
                    page.goto(arg, wait_until="domcontentloaded")
                    page.bring_to_front()
                else:
                    self._run(arg)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                if kind == "job":
                    arg.update(status="error", message=_short(e))

    def _context(self):
        """Persistent browser context; relaunched if the user closed the window."""
        if self._ctx is not None:
            try:
                self._ctx.pages  # noqa: B018 - raises if closed
                if self._ctx.browser is None or self._ctx.browser.is_connected():
                    return self._ctx
            except Exception:  # noqa: BLE001
                pass
            self._ctx = None
        if self._pw is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
        PROFILE_DIR.mkdir(exist_ok=True)
        opts = dict(user_data_dir=str(PROFILE_DIR), headless=False, accept_downloads=False,
                    viewport={"width": 1100, "height": 800})
        try:  # real Chrome copes best with Microsoft SSO / conditional access
            self._ctx = self._pw.chromium.launch_persistent_context(channel="chrome", **opts)
        except Exception:  # noqa: BLE001
            self._ctx = self._pw.chromium.launch_persistent_context(**opts)
        self._ctx.on("close", lambda _: setattr(self, "_ctx", None))
        return self._ctx

    def _run(self, job: Job):
        job.update(status="running", message="Opening link in browser…", progress=-1)
        out_dir = Path(job.out_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)

        ctx = self._context()
        page = ctx.new_page()
        children: list[Job] | None = None
        manifests: list[str] = []
        list_data: list = []  # responses carrying ListSchema[".videoManifestUrl"]
        page.on("request", lambda r: manifests.append(r.url) if "videomanifest" in r.url else None)

        def on_response(resp):
            if any(k in resp.url for k in LIST_DATA_CALLS):
                list_data.append(resp)

        page.on("response", on_response)

        try:
            target = job.url
            parsed = urlparse(target)
            # A direct file URL would trigger a browser download; view it via stream.aspx instead.
            if parsed.path.lower().endswith(VIDEO_EXT) and "stream.aspx" not in parsed.path.lower():
                site = _site_of(unquote(parsed.path))
                target = (f"{parsed.scheme}://{parsed.netloc}{site}/_layouts/15/stream.aspx"
                          f"?id={quote(unquote(parsed.path))}")
            page.goto(target, wait_until="domcontentloaded", timeout=60_000)

            # Wait until we are on SharePoint (past any login) and know the file/folder path.
            deadline = time.time() + SIGN_IN_TIMEOUT
            file_path = folder_path = None
            while time.time() < deadline:
                host = urlparse(page.url).netloc.lower()
                if any(h in host for h in LOGIN_HOSTS):
                    job.update(status="waiting_login",
                               message="Sign in in the browser window that opened…")
                    page.bring_to_front()
                else:
                    if job.status == "waiting_login":
                        job.update(status="running", message="Signed in, opening link…")
                    file_path = _file_path_from(page.url) or _file_path_from(job.url)
                    folder_path = _folder_path_from(page.url)
                    if file_path or folder_path or manifests:
                        break
                page.wait_for_timeout(1000)
            else:
                raise RuntimeError("Timed out waiting for sign-in / page load")

            pr = urlparse(page.url)
            origin = f"{pr.scheme}://{pr.netloc}"

            # Folder link: list its videos, then download them one by one (below).
            if folder_path and not manifests:
                children = self._expand_folder(job, page, origin, folder_path)
                return
            if not file_path and not manifests:
                raise RuntimeError("This link is not a video or a folder.")

            ext = Path(file_path).suffix.lower() if file_path else ""
            name = self._assign_name(job) + (ext if ext in VIDEO_EXT else ".mp4")
            dest = _unique(out_dir / _safe(name))
            job.update(filename=dest.name, source=job.source or (Path(file_path).name if file_path else ""))

            # 1) Original file via SharePoint's download endpoint.
            if file_path:
                job.update(message="Trying direct download of the original file…")
                if self._direct(job, ctx, origin, file_path, dest):
                    return

            # 2) View-only: fetch the DASH stream the web player uses.
            job.update(message="Download is blocked for this file; locating the video stream…",
                       progress=-1)
            candidates = self._manifest_candidates(page, file_path, manifests, list_data)
            if not candidates:
                raise RuntimeError("Could not download the file and no video stream was found. "
                                   "Check you can play this video in the browser window.")
            errors = []
            for m in candidates:
                try:
                    self._stream(job, m, dest)
                    return
                except Exception as e:  # noqa: BLE001
                    errors.append(_short(e))
                    job.update(message="Stream attempt failed, trying another…", progress=-1)
            raise RuntimeError("Stream download failed: " + errors[-1])
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
            if children is not None:
                self._run_children(job, children)

    # ---------- folders ----------
    def _expand_folder(self, job: Job, page, origin: str, folder_path: str) -> list[Job]:
        job.update(kind="folder", filename=Path(folder_path).name or folder_path,
                   message="Listing videos in folder…")
        files = _list_folder(page, folder_path, job.recursive)
        videos = [f for f in files if f["path"].lower().endswith(VIDEO_EXT)]
        key = {
            "created": lambda f: (f.get("created") or "", _natural(f["path"])),
            "modified": lambda f: (f.get("modified") or "", _natural(f["path"])),
        }.get(job.sort, lambda f: _natural(f["path"]))
        videos.sort(key=key)
        if not videos:
            raise RuntimeError(f"No videos found in this folder ({len(files)} other files)."
                               + ("" if job.recursive else " Tick 'Include subfolders' to look deeper."))
        children = []
        with self._lock:
            at = self.order.index(job.id) + 1
            for f in videos:
                site = _site_of(f["path"])
                c = Job(url=f"{origin}{site}/_layouts/15/stream.aspx?id={quote(f['path'])}",
                        out_dir=job.out_dir, prefix=job.prefix, parent=job.id,
                        source=Path(f["path"]).name, filename=Path(f["path"]).name)
                self.jobs[c.id] = c
                children.append(c)
            self.order[at:at] = [c.id for c in children]
        job.update(message=f"Found {len(children)} videos; downloading one by one…", progress=0.0)
        return children

    def _run_children(self, job: Job, children: list[Job]):
        ok = failed = 0
        for i, c in enumerate(children):
            job.update(message=f"Downloading {i + 1} of {len(children)}: {c.source}",
                       progress=i / len(children))
            try:
                self._run(c)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                c.update(status="error", message=_short(e))
            if c.status == "done":
                ok += 1
            else:
                failed += 1
        job.update(status="done" if ok else "error", progress=1.0,
                   message=f"{len(children)} videos: {ok} downloaded"
                           + (f", {failed} failed" if failed else ""))

    def _manifest_candidates(self, page, file_path, manifests, list_data) -> list[str]:
        """Manifest URLs to try, best first.

        a) Built from the list-data response's `.videoManifestUrl` template (the
           approach Sharedown uses) - works even if the player never starts.
        b) The manifest the player actually requested, sniffed from the network.
        """
        def built():
            out = []
            for resp in reversed(list_data):
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    continue
                url = _manifest_from_list_data(data, page, file_path)
                if url:
                    out.append(url)
            return out

        out = built()
        if not out and not manifests:
            # Nothing captured yet: reload once so the listeners catch the
            # page's list-data call, then give the player a nudge.
            page.reload(wait_until="domcontentloaded")
            end = time.time() + MANIFEST_TIMEOUT
            while time.time() < end:
                page.wait_for_timeout(1500)
                out = built()
                if out or manifests:
                    break
                _nudge_play(page)
        for m in reversed(manifests):
            out += [_normalize_manifest(m), _clean_manifest(m)]
        seen, uniq = set(), []
        for u in out:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    def _direct(self, job: Job, ctx, origin: str, file_path: str, dest: Path) -> bool:
        site = _site_of(file_path)
        url = f"{origin}{site}/_layouts/15/download.aspx?SourceUrl={quote(file_path)}"
        s = requests.Session()
        for c in ctx.cookies([origin]):
            s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
        s.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        try:
            r = s.get(url, stream=True, timeout=60, allow_redirects=True)
        except requests.RequestException:
            return False
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "text/html" in ctype or "json" in ctype:
            r.close()
            return False
        total = int(r.headers.get("content-length") or 0)
        done = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        job.update(method="original file", message="Downloading original file…")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                job.update(progress=(done / total) if total else -1,
                           message=f"Downloading original file… {_mb(done)}"
                                   + (f" / {_mb(total)}" if total else ""))
        tmp.rename(dest)
        job.update(status="done", progress=1.0, path=str(dest), message=f"Saved {_mb(done)}")
        return True

    def _stream(self, job: Job, manifest: str, dest: Path):
        import yt_dlp

        job.update(method="video stream (DASH)")
        part = {"n": 0}

        def hook(d):
            if d["status"] == "downloading":
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                label = "video" if part["n"] == 0 else "audio"
                job.update(progress=(got / tot) if tot else -1,
                           message=f"Downloading {label} stream… {_mb(got)}"
                                   + (f" / {_mb(tot)}" if tot else ""))
            elif d["status"] == "finished":
                part["n"] += 1
                job.update(message="Merging audio and video…", progress=-1)

        opts = {
            "outtmpl": str(dest.with_suffix("")) + ".%(ext)s",
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "progress_hooks": [hook],
            "quiet": True,
            "noprogress": True,
            "concurrent_fragment_downloads": 4,
        }
        ff = ffmpeg_path()
        if ff:
            opts["ffmpeg_location"] = ff
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([manifest])
        final = dest.with_suffix(".mp4")
        if not final.exists():
            cands = sorted(dest.parent.glob(dest.stem + ".*"), key=lambda p: p.stat().st_mtime)
            final = cands[-1] if cands else final
        job.update(status="done", progress=1.0, path=str(final), filename=final.name,
                   message=f"Saved {_mb(final.stat().st_size) if final.exists() else ''}")


# ---------- helpers ----------
def ffmpeg_path() -> str | None:
    """System ffmpeg if installed, else the copy bundled with imageio-ffmpeg."""
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None



def _file_path_from(url: str) -> str | None:
    """Server-relative path of the file from a stream.aspx / AllItems.aspx / direct URL."""
    p = urlparse(url)
    qs = parse_qs(p.query)
    for key in ("id", "SourceUrl", "sourceurl"):
        if key in qs and qs[key][0].startswith("/"):
            return unquote(qs[key][0])
    path = unquote(p.path)
    if path.lower().endswith(VIDEO_EXT):
        return path
    return None


def _folder_path_from(url: str) -> str | None:
    """Server-relative folder path from a library/OneDrive folder view URL, else None."""
    p = urlparse(url)
    if "stream.aspx" in p.path.lower():
        return None
    qs = parse_qs(p.query)
    for key in ("id", "RootFolder"):
        if key in qs and qs[key][0].startswith("/"):
            path = unquote(qs[key][0]).rstrip("/")
            # A file previewed from a library view has id=<file>&parent=<folder>.
            return None if path.lower().endswith(VIDEO_EXT) or "parent" in qs else path
    path = unquote(p.path)
    m = re.match(r"^(.*?)/Forms/[^/]+\.aspx$", path, re.I)  # library root view
    return m.group(1) if m else None


_LIST_JS = r"""
async ({site, folder, recursive, listUrl}) => {
  const H = {Accept: 'application/json;odata=nometadata'};
  const q = s => encodeURIComponent(s.replace(/'/g, "''"));
  const api = location.origin + site + '/_api/web';
  async function rest(path) {
    const base = `${api}/GetFolderByServerRelativePath(decodedurl='${q(path)}')`;
    const r = await fetch(`${base}/Files?$select=ServerRelativeUrl,TimeCreated,TimeLastModified&$top=5000`, {headers: H});
    if (!r.ok) throw new Error('REST ' + r.status);
    let out = (await r.json()).value.map(f => ({path: f.ServerRelativeUrl, created: f.TimeCreated, modified: f.TimeLastModified}));
    if (recursive) {
      const d = await fetch(`${base}/Folders?$select=Name,ServerRelativeUrl&$top=5000`, {headers: H});
      if (d.ok) for (const sub of (await d.json()).value)
        if (sub.Name !== 'Forms') out = out.concat(await rest(sub.ServerRelativeUrl));
    }
    return out;
  }
  try { return {files: await rest(folder)}; } catch (e) { var restErr = String(e); }
  // Fallback: the call the SharePoint UI itself uses (works for some sharing-link access).
  const digest = (window._spPageContextInfo || {}).formDigestValue
              || (document.getElementById('__REQUESTDIGEST') || {}).value || '';
  const u = `${api}/GetList(@a1)/RenderListDataAsStream?@a1='${q(listUrl)}'&RootFolder=${encodeURIComponent(folder)}`;
  const view = `<View${recursive ? ' Scope="RecursiveAll"' : ''}><ViewFields><FieldRef Name="FileRef"/>`
             + `<FieldRef Name="FSObjType"/><FieldRef Name="Created"/><FieldRef Name="Modified"/></ViewFields>`
             + `<RowLimit Paged="TRUE">5000</RowLimit></View>`;
  const r = await fetch(u, {method: 'POST', body: JSON.stringify({parameters: {RenderOptions: 2, ViewXml: view}}),
    headers: {...H, 'Content-Type': 'application/json;odata=nometadata', ...(digest ? {'X-RequestDigest': digest} : {})}});
  if (!r.ok) return {error: `${restErr}; list ${r.status}`};
  const j = await r.json();
  return {files: (j.Row || []).filter(x => String(x.FSObjType) === '0')
    .map(x => ({path: x.FileRef, created: x['Created.'] || x.Created, modified: x['Modified.'] || x.Modified}))};
}
"""


def _list_folder(page, folder_path: str, recursive: bool) -> list[dict]:
    """Files in a SharePoint folder, as [{path, created, modified}], via the signed-in page."""
    site = _site_of(folder_path)
    nsite = len([s for s in site.split("/") if s])
    parts = folder_path.strip("/").split("/")
    list_url = "/" + "/".join(parts[: nsite + 1])
    res = page.evaluate(_LIST_JS, {"site": site, "folder": folder_path,
                                   "recursive": recursive, "listUrl": list_url})
    if res.get("error"):
        raise RuntimeError(f"Could not list the folder ({res['error']}). "
                           "Check you can open it in the browser window.")
    return res["files"]


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _site_of(file_path: str) -> str:
    m = re.match(r"^(/(?:sites|teams|personal)/[^/]+)", file_path, re.I)
    return m.group(1) if m else ""


MANIFEST_PARAMS = {"action": "Access", "part": "Index", "format": "dash", "useScf": "True",
                   "pretranscode": "0", "transcodeahead": "0"}


def _normalize_manifest(url: str) -> str:
    """Force the query parameters that make videomanifest return a full DASH index."""
    p = urlparse(url)
    qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
          if k not in MANIFEST_PARAMS and k != "altManifestMetadata"]
    qs += list(MANIFEST_PARAMS.items())
    return urlunparse(p._replace(query=urlencode(qs, quote_via=quote)))


def _manifest_from_list_data(data: dict, page, file_path: str | None) -> str | None:
    """Fill ListSchema['.videoManifestUrl'] from a RenderListDataAsStream response."""
    schema = data.get("ListSchema") or {}
    tmpl = schema.get(".videoManifestUrl")
    if not tmpl:
        return None
    list_data = data.get("ListData") or {}
    sp_item = list_data.get("CurrentFolderSpItemUrl") or ""
    if not sp_item and file_path:
        for row in list_data.get("Row") or []:
            if row.get("FileRef", "").lower() == file_path.lower() and row.get(".spItemUrl"):
                sp_item = row[".spItemUrl"]
                break
    if not sp_item and file_path:
        sp_item = _fetch_sp_item_url(page, file_path)
    values = {
        "{.mediaBaseUrl}": schema.get(".mediaBaseUrl", ""),
        "{.fileType}": Path(file_path).suffix.lstrip(".").lower() if file_path else "mp4",
        "{.callerStack}": schema.get(".callerStack", ""),
        "{.spItemUrl}": sp_item,
        "{.driveAccessToken}": schema.get(".driveAccessToken", ""),
    }
    if not all(values[k] for k in ("{.mediaBaseUrl}", "{.spItemUrl}", "{.driveAccessToken}")):
        return None
    for k, v in values.items():
        tmpl = tmpl.replace(k, v)
    return _normalize_manifest(tmpl)


def _fetch_sp_item_url(page, file_path: str) -> str:
    """Ask SharePoint (as the signed-in page) for the file's .spItemUrl."""
    parts = file_path.strip("/").split("/")
    site = _site_of(file_path)
    nsite = len([s for s in site.split("/") if s])
    if len(parts) <= nsite + 1:
        return ""
    list_url = "/" + "/".join(parts[: nsite + 1])  # e.g. /sites/Team/Shared Documents
    origin = "{0.scheme}://{0.netloc}".format(urlparse(page.url))
    api = (f"{origin}{site}/_api/web/GetList(@a1)/RenderListDataAsStream"
           f"?@a1={quote(chr(39) + list_url + chr(39))}&RootFolder={quote(file_path)}")
    try:
        data = page.evaluate(
            "async (u) => { const r = await fetch(u, {method: 'POST', headers: "
            "{'Accept': 'application/json'}}); return r.ok ? r.json() : null; }", api)
    except Exception:  # noqa: BLE001
        return ""
    return (data or {}).get("CurrentFolderSpItemUrl", "") or ""


def _clean_manifest(url: str) -> str:
    # The player appends extra parameters; trimming after format=dash gives a
    # manifest yt-dlp/ffmpeg can read directly (the tempauth token stays in).
    i = url.find("format=dash")
    return url[: i + len("format=dash")] if i != -1 else url


def _nudge_play(page):
    try:
        page.evaluate("() => { const v = document.querySelector('video'); if (v) v.play(); }")
    except Exception:  # noqa: BLE001
        pass


def _safe(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip(" .")
    return name[:180] or "video.mp4"


def _unique(p: Path) -> Path:
    if not p.exists() and not p.with_suffix(".mp4").exists():
        return p
    i = 2
    while True:
        c = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not c.exists():
            return c
        i += 1


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def _short(e: Exception) -> str:
    s = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return s[:300]
