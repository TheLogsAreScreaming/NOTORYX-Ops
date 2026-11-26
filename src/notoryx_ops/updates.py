"""Checks for newer NOTORYX Ops versions and add-ons, and fetches them with checksums.

Everything comes from the public NOTORYX-Ops-Releases repository on GitHub. Releases are
listed from its public releases feed (releases.atom) rather than the GitHub API, which only
allows 60 unauthenticated calls an hour per network. Each release carries a SHA256SUMS
file: it names the release's files, and every download is checked against it before use.
Nothing is installed without someone pressing a button in the dashboard.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from . import __version__
from .net import urlopen

log = logging.getLogger(__name__)

RELEASES_REPO = "TheLogsAreScreaming/NOTORYX-Ops-Releases"
RELEASES_URL = f"https://github.com/{RELEASES_REPO}/releases"
FEED = f"{RELEASES_URL}.atom"
DOWNLOAD = f"{RELEASES_URL}/download"
CACHE_SECONDS = 3600

# Add-ons the dashboard can install: wheel name prefix -> package name and what it is.
KNOWN_ADDONS = {
    "notoryx_ops_meshtastic": {
        "package": "notoryx-ops-meshtastic",
        "name": "Meshtastic",
        "license": "GPL-3.0",
        "about": "The station's own Meshtastic radio, remote admin over the air, settings templates "
                 "and an optional Meshtastic ⇄ MeshCore channel bridge.",
    },
}

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
_cache: dict[str, Any] = {"at": 0.0, "releases": None}


class UpdateError(Exception):
    pass


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION.match(text or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def installed_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _get(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "notoryx-ops"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def _notes_text(html_text: str) -> str:
    """Release notes from the feed's HTML, back as simple markdown-ish text (headings and bullets kept)."""
    t = html.unescape(html_text or "")
    t = re.sub(r"(?i)<h[1-6][^>]*>", "\n## ", t)
    t = re.sub(r"(?i)<li[^>]*>", "\n- ", t)
    t = re.sub(r"(?i)<(br|/p|/h[1-6]|/li|/ul|/tr)[^>]*>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _sums_for(tag: str) -> dict[str, str]:
    """A release's SHA256SUMS: file name -> checksum. Also the list of its files."""
    text = _get(f"{DOWNLOAD}/{tag}/SHA256SUMS", timeout=30).decode()
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def releases(force: bool = False) -> list[dict[str, Any]]:
    """App releases (tags starting with v), newest first, from the releases feed. Cached for an hour;
    a failed check keeps showing the last good answer."""
    fresh = time.time() - _cache["at"] < CACHE_SECONDS
    if _cache["releases"] is not None and (fresh and not force):
        return _cache["releases"]
    try:
        root = ET.fromstring(_get(FEED))
    except Exception as exc:  # noqa: BLE001
        if _cache["releases"] is not None:
            return _cache["releases"]
        raise UpdateError(f"Couldn't reach GitHub to check for updates: {exc}") from exc
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        link = e.find("a:link", ns)
        url = link.get("href", "") if link is not None else ""
        tag = url.rsplit("/tag/", 1)[-1] if "/tag/" in url else ""
        v = parse_version(tag)
        if not v:
            continue
        out.append({"tag": tag, "version": ".".join(map(str, v)), "key": v,
                    "name": (e.findtext("a:title", "", ns) or tag), "url": url or RELEASES_URL,
                    "notes": _notes_text(e.findtext("a:content", "", ns)), "published": e.findtext("a:updated", "", ns),
                    "assets": None})
    out.sort(key=lambda r: r["key"], reverse=True)
    if out:
        try:
            sums = _sums_for(out[0]["tag"])
            out[0]["assets"] = {name: f"{DOWNLOAD}/{out[0]['tag']}/{name}" for name in [*sums, "SHA256SUMS"]}
            out[0]["sums"] = sums
        except Exception as exc:  # noqa: BLE001
            if _cache["releases"] is not None:
                return _cache["releases"]
            raise UpdateError(f"Couldn't read the newest release's file list: {exc}") from exc
    _cache.update(at=time.time(), releases=out)
    return out


def summary(force: bool = False) -> dict[str, Any]:
    """What is installed, what the newest release has, and which add-ons it offers."""
    rels = releases(force)
    latest = rels[0] if rels else None
    current = parse_version(__version__) or (0, 0, 0)
    addons = []
    if latest:
        for asset in latest["assets"] or {}:
            for prefix, meta in KNOWN_ADDONS.items():
                if asset.startswith(prefix + "-") and asset.endswith(".whl"):
                    avail = asset[len(prefix) + 1:].split("-")[0]
                    have = installed_version(meta["package"])
                    addons.append({"id": prefix, "asset": asset, "available": avail, "installed": have,
                                   "newer": bool(have) and (parse_version(avail) or (0,)) > (parse_version(have) or (0,)),
                                   **{k: meta[k] for k in ("name", "license", "about")}})
    return {
        "current": __version__,
        "latest": latest and {k: latest[k] for k in ("tag", "version", "name", "url", "notes", "published")},
        "newer": bool(latest) and latest["key"] > current,
        "mac_asset": latest and next((a for a in latest["assets"] or {} if a.endswith("-mac.zip")), None),
        "addons": addons,
        "releases_url": RELEASES_URL,
    }


def download(asset: str, folder: Path, progress: Callable[[int, int | None], None] | None = None) -> Path:
    """Download one file of the newest release and check it against that release's SHA256SUMS."""
    rels = releases()
    if not rels:
        raise UpdateError("No releases found.")
    rel = rels[0]
    if asset not in (rel["assets"] or {}) or "/" in asset or asset.startswith("."):
        raise UpdateError(f"The newest release has no file called {asset}.")
    want = (rel.get("sums") or {}).get(asset)
    if not want:
        raise UpdateError(f"{asset} isn't listed in SHA256SUMS, so it can't be checked.")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / asset
    tmp = folder / (asset + ".part")
    h = hashlib.sha256()
    req = urllib.request.Request(rel["assets"][asset], headers={"User-Agent": "notoryx-ops"})
    with urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = r.headers.get("Content-Length") if hasattr(r, "headers") else None
        total = int(total) if total and str(total).isdigit() else None
        done = 0
        while chunk := r.read(1 << 16):
            h.update(chunk)
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != want:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"{asset} didn't match its checksum, so it was thrown away. Try again later.")
    tmp.replace(dest)
    log.info("downloaded and checked %s", dest)
    return dest


def pip(*args: str) -> str:
    """Run pip in the station's own Python environment."""
    p = subprocess.run([sys.executable, "-m", "pip", "--disable-pip-version-check", *args],
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
        raise UpdateError("pip failed: " + " ".join(tail))
    return p.stdout


def install_addon(addon_id: str, folder: Path) -> str:
    info = next((a for a in summary()["addons"] if a["id"] == addon_id), None)
    if not info:
        raise UpdateError("The newest release doesn't offer that add-on.")
    wheel = download(info["asset"], folder)
    pip("install", "--quiet", "--upgrade", str(wheel))
    return info["available"]


def remove_addon(addon_id: str) -> None:
    meta = KNOWN_ADDONS.get(addon_id)
    if not meta:
        raise UpdateError("Unknown add-on.")
    pip("uninstall", "--yes", meta["package"])
