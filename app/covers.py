"""Cover art proxy + disk cache. Cover Art Archive first, local folder.jpg as fallback."""
import logging
from pathlib import Path

import httpx

from . import config, db

log = logging.getLogger("covers")
_client = httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=30, follow_redirects=True)
LOCAL_NAMES = ["cover", "folder", "front", "album"]


def _local_cover(rg_mbid: str) -> Path | None:
    with db.conn() as c:
        row = c.execute("SELECT path FROM files WHERE rg_mbid = ? LIMIT 1", (rg_mbid,)).fetchone()
    if not row:
        return None
    folder = (config.MUSIC_DIR / row["path"]).parent
    for name in LOCAL_NAMES:
        for ext in ("jpg", "jpeg", "png"):
            p = folder / f"{name}.{ext}"
            if p.exists():
                return p
    return None


def release_group_cover(rg_mbid: str, size: int = 250) -> tuple[bytes, str] | None:
    cache = config.COVER_DIR / f"rg-{rg_mbid}-{size}.jpg"
    miss = config.COVER_DIR / f"rg-{rg_mbid}.miss"
    if cache.exists():
        return cache.read_bytes(), "image/jpeg"
    if not miss.exists():
        try:
            r = _client.get(f"{config.CAA_BASE}/release-group/{rg_mbid}/front-{size}")
            if r.status_code == 200 and r.content:
                cache.write_bytes(r.content)
                return r.content, r.headers.get("content-type", "image/jpeg")
            if r.status_code == 404:
                miss.touch()
        except httpx.HTTPError as e:
            log.warning("cover fetch failed: %s", e)
            return None  # transient: don't cache the miss
    local = _local_cover(rg_mbid)
    if local:
        return local.read_bytes(), "image/png" if local.suffix == ".png" else "image/jpeg"
    return None


def artist_cover(artist_mbid: str, rg_ids: list[str]) -> tuple[bytes, str] | None:
    """MusicBrainz has no artist photos; use the newest owned album cover."""
    for rg in rg_ids:
        res = release_group_cover(rg)
        if res:
            return res
    return None
