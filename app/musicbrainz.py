"""Minimal rate-limited MusicBrainz web service client (max 1 request/second)."""
import logging
import threading
import time

import httpx

from . import config

log = logging.getLogger("musicbrainz")

_lock = threading.Lock()
_last = 0.0
_client = httpx.Client(headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"}, timeout=40,
                       follow_redirects=True)


def _get(path: str, params: dict | None = None, retries: int = 5):
    global _last
    params = {**(params or {}), "fmt": "json"}
    url = f"{config.MB_BASE}/{path}"
    for attempt in range(retries):
        with _lock:
            wait = config.MB_MIN_INTERVAL - (time.monotonic() - _last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = _client.get(url, params=params)
            except httpx.HTTPError as e:
                log.warning("MB request failed (%s), retry %d", e, attempt + 1)
                _last = time.monotonic()
                time.sleep(3 * (attempt + 1))
                continue
            _last = time.monotonic()
        if r.status_code in (429, 502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"MusicBrainz request failed: {path}")


def get_artist(mbid: str) -> dict | None:
    return _get(f"artist/{mbid}")


def browse_release_groups(artist_mbid: str) -> list[dict]:
    out, offset = [], 0
    while True:
        data = _get("release-group", {"artist": artist_mbid, "limit": 100, "offset": offset})
        if not data:
            break
        batch = data.get("release-groups", [])
        out.extend(batch)
        offset += len(batch)
        if not batch or offset >= data.get("release-group-count", 0):
            break
    return out


def get_release_group(mbid: str) -> dict | None:
    return _get(f"release-group/{mbid}", {"inc": "artist-credits"})


def get_release_group_of_release(release_mbid: str) -> dict | None:
    data = _get(f"release/{release_mbid}", {"inc": "release-groups artist-credits"})
    return data.get("release-group") if data else None


def browse_releases(rg_mbid: str) -> list[dict]:
    """Releases of a release group incl. media (track counts), no recordings."""
    out, offset = [], 0
    while True:
        data = _get("release", {"release-group": rg_mbid, "inc": "media", "limit": 100, "offset": offset})
        if not data:
            break
        batch = data.get("releases", [])
        out.extend(batch)
        offset += len(batch)
        if not batch or offset >= data.get("release-count", 0):
            break
    return out


def get_release_tracks(release_mbid: str) -> dict | None:
    return _get(f"release/{release_mbid}", {"inc": "recordings media"})
