"""Computes ownership / completion by matching library files against cached MusicBrainz track lists."""
import re
import threading
from collections import defaultdict

from . import db, sync

_cache = {"version": -1, "data": None}
_lock = threading.Lock()


def _norm_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def match_tracks(tracks, files):
    """Returns (rows, extra_files). Each row: track dict + owned flag + matching file."""
    by_rec, by_trk, by_title = {}, {}, {}
    for f in files:
        if f["recording_mbid"]:
            by_rec.setdefault(f["recording_mbid"], f)
        if f["track_mbid"]:
            by_trk.setdefault(f["track_mbid"], f)
        by_title.setdefault(_norm_title(f["title"]), f)
    used, rows = set(), []
    for t in tracks:
        f = by_trk.get(t["track_mbid"]) or by_rec.get(t["recording_mbid"])
        if not f:
            cand = by_title.get(_norm_title(t["title"]))
            if cand and not cand["recording_mbid"]:  # title fallback only for untagged files
                f = cand
        if f:
            used.add(f["path"])
        rows.append({**t, "file": f})
    extra = [f for f in files if f["path"] not in used]
    return rows, extra


def _build():
    settings = db.get_settings()
    with db.conn() as c:
        artists = {r["mbid"]: dict(r) for r in c.execute("SELECT * FROM artists")}
        rgs = [dict(r) for r in c.execute("SELECT * FROM release_groups")]
        state = {r["rg_mbid"]: dict(r) for r in c.execute("SELECT * FROM rg_state")}
        files = [dict(r) for r in c.execute(
            "SELECT path, title, artist_mbid, rg_mbid, release_mbid, recording_mbid, track_mbid FROM files")]
        trk = defaultdict(list)
        for r in c.execute("SELECT * FROM tracks ORDER BY disc, position"):
            trk[r["release_mbid"]].append(dict(r))

    files_by_rg = defaultdict(list)
    files_by_artist = defaultdict(list)
    unmatched = 0
    for f in files:
        if f["rg_mbid"]:
            files_by_rg[f["rg_mbid"]].append(f)
        else:
            unmatched += 1
        if f["artist_mbid"]:
            files_by_artist[f["artist_mbid"]].append(f)

    rg_stats = {}  # (artist, rg) -> stats
    per_artist = defaultdict(list)
    for g in rgs:
        owned_files = [f for f in files_by_rg.get(g["mbid"], []) if f["artist_mbid"] == g["artist_mbid"]]
        st = state.get(g["mbid"], {})
        tracks = trk.get(st.get("release_mbid"), []) if st.get("tracks_fetched") else []
        secondary = [s for s in (g["secondary_types"] or "").split(",") if s]
        if tracks:
            rows, extra = match_tracks(tracks, owned_files)
            owned = sum(1 for r in rows if r["file"])
            total = len(tracks)
        else:
            owned = len({f["recording_mbid"] or f["path"] for f in owned_files})
            total = st.get("track_count")
            if total is not None:
                owned = min(owned, total)
        s = {
            "mbid": g["mbid"], "artist_mbid": g["artist_mbid"], "title": g["title"],
            "primary_type": g["primary_type"] or "Other", "secondary_types": secondary,
            "date": g["first_release_date"], "owned": owned, "total": total,
            "files": len(owned_files), "in_library": bool(owned_files),
            "wanted": sync.is_wanted(g["primary_type"], secondary, settings) or bool(owned_files),
        }
        rg_stats[(g["artist_mbid"], g["mbid"])] = s
        per_artist[g["artist_mbid"]].append(s)

    out = []
    for mbid in set(artists) | set(files_by_artist):
        a = artists.get(mbid) or {"mbid": mbid, "name": mbid, "sort_name": mbid}
        if mbid not in files_by_artist:
            continue  # only artists that have music in the library
        al = [s for s in per_artist.get(mbid, []) if s["wanted"]]
        known = [s for s in al if s["total"] is not None]
        owned_tracks = sum(s["owned"] for s in known)
        total_tracks = sum(s["total"] for s in known)
        out.append({
            "mbid": mbid, "name": a.get("name") or mbid, "sort_name": a.get("sort_name") or a.get("name") or mbid,
            "disambiguation": a.get("disambiguation"), "type": a.get("type"), "country": a.get("country"),
            "synced": bool(a.get("synced_at")),
            "albums_total": len(al), "albums_owned": sum(1 for s in al if s["in_library"]),
            "tracks_owned": owned_tracks, "tracks_total": total_tracks,
            "files": len(files_by_artist[mbid]),
            "percent": round(100 * owned_tracks / total_tracks) if total_tracks else (0 if al else None),
        })
    return {"artists": {a["mbid"]: a for a in out}, "rg": rg_stats, "per_artist": per_artist,
            "unmatched": unmatched, "files": len(files)}


def snapshot():
    with _lock:
        v = db.data_version()
        if _cache["version"] != v or _cache["data"] is None:
            _cache["data"] = _build()
            _cache["version"] = v
        return _cache["data"]
