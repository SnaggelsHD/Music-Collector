"""Fetches artist / release group / track data from MusicBrainz and caches it in SQLite."""
import logging
import time
from collections import Counter

from . import config, db, musicbrainz as mb

log = logging.getLogger("sync")


def is_wanted(primary: str | None, secondary: list[str], settings: dict) -> bool:
    if (primary or "Other") not in settings["primary_types"]:
        return False
    allowed = set(settings["secondary_types"])
    if not secondary:
        return "Studio" in allowed
    return all(s in allowed for s in secondary)


def pick_release(releases: list[dict], prefer: str | None = None) -> dict | None:
    """Choose the most 'standard' release of a release group."""
    if not releases:
        return None
    if prefer:
        for r in releases:
            if r["id"] == prefer:
                return r

    def tcount(r):
        return sum(m.get("track-count", 0) for m in r.get("media", []))

    pool = [r for r in releases if r.get("status") == "Official" and tcount(r) > 0] \
        or [r for r in releases if tcount(r) > 0] or releases
    mode_count = Counter(tcount(r) for r in pool).most_common()
    best = max(c for _, c in mode_count)
    target = min(n for n, c in mode_count if c == best)  # standard edition, not the deluxe one
    cands = [r for r in pool if tcount(r) == target]
    cands.sort(key=lambda r: r.get("date") or "9999")
    return cands[0]


def _upsert_rgs(c, artist_mbid, rgs):
    c.executemany(
        """INSERT OR REPLACE INTO release_groups
           (mbid, artist_mbid, title, primary_type, secondary_types, first_release_date)
           VALUES (?,?,?,?,?,?)""",
        [(g["id"], artist_mbid, g.get("title"), g.get("primary-type"),
          ",".join(g.get("secondary-types") or []), g.get("first-release-date")) for g in rgs])


def _store_release_tracks(release_mbid: str, rg_mbid: str) -> bool:
    data = mb.get_release_tracks(release_mbid)
    if not data:
        return False
    rows = []
    for m in data.get("media", []):
        disc = m.get("position") or 1
        for t in m.get("tracks", []):
            rows.append((release_mbid, disc, t.get("position"), t.get("id"),
                         (t.get("recording") or {}).get("id"), t.get("title"),
                         t.get("length") or (t.get("recording") or {}).get("length")))
    with db.conn() as c:
        c.execute("DELETE FROM tracks WHERE release_mbid = ?", (release_mbid,))
        c.executemany("INSERT OR REPLACE INTO tracks VALUES (?,?,?,?,?,?,?)", rows)
        c.execute("INSERT OR REPLACE INTO rg_state VALUES (?,?,?,?,?)",
                  (rg_mbid, release_mbid, len(rows), 1, time.time()))
    db.bump_version()
    return True


def _store_release_choice(rg_mbid: str) -> dict | None:
    releases = mb.browse_releases(rg_mbid)
    rel = pick_release(releases)
    with db.conn() as c:
        if rel:
            n = sum(m.get("track-count", 0) for m in rel.get("media", []))
            c.execute("INSERT OR REPLACE INTO rg_state VALUES (?,?,?,?,?)", (rg_mbid, rel["id"], n, 0, time.time()))
        else:
            c.execute("INSERT OR REPLACE INTO rg_state VALUES (?,?,?,?,?)", (rg_mbid, None, None, 0, time.time()))
    db.bump_version()
    return rel


def owned_release(c, rg_mbid: str) -> str | None:
    """The release MBID the user's files for this group mostly belong to."""
    row = c.execute(
        """SELECT release_mbid, COUNT(*) n FROM files WHERE rg_mbid = ? AND release_mbid IS NOT NULL
           GROUP BY release_mbid ORDER BY n DESC LIMIT 1""", (rg_mbid,)).fetchone()
    return row["release_mbid"] if row else None


def ensure_tracks(rg_mbid: str) -> None:
    """Make sure the track list for a release group's representative release is cached."""
    with db.conn() as c:
        st = c.execute("SELECT * FROM rg_state WHERE rg_mbid = ?", (rg_mbid,)).fetchone()
        owned = owned_release(c, rg_mbid)
    if owned and (not st or st["release_mbid"] != owned or not st["tracks_fetched"]):
        if _store_release_tracks(owned, rg_mbid):
            return
    st_rel = st["release_mbid"] if st else None
    if not st or (not st_rel and not st["fetched_at"]):
        rel = _store_release_choice(rg_mbid)
        st_rel = rel["id"] if rel else None
    if st_rel and not (st and st["tracks_fetched"]):
        _store_release_tracks(st_rel, rg_mbid)


def sync_artist(mbid: str) -> None:
    """Phase A: artist info, release group list, track lists of the groups the user owns."""
    info = mb.get_artist(mbid)
    with db.conn() as c:
        if info:
            c.execute(
                """INSERT OR REPLACE INTO artists
                   (mbid, name, sort_name, disambiguation, type, country, begin, end, synced_at, counts_at)
                   VALUES (?,?,?,?,?,?,?,?,?,(SELECT counts_at FROM artists WHERE mbid = ?))""",
                (mbid, info.get("name"), info.get("sort-name"), info.get("disambiguation"),
                 info.get("type"), info.get("country") or (info.get("area") or {}).get("name"),
                 (info.get("life-span") or {}).get("begin"), (info.get("life-span") or {}).get("end"),
                 time.time(), mbid))
        else:
            c.execute("UPDATE artists SET synced_at = ? WHERE mbid = ?", (time.time(), mbid))

    if mbid != config.VARIOUS_ARTISTS and info:
        rgs = mb.browse_release_groups(mbid)
        with db.conn() as c:
            c.execute("DELETE FROM release_groups WHERE artist_mbid = ?", (mbid,))
            _upsert_rgs(c, mbid, rgs)

    # files that only know their release: resolve the release group
    with db.conn() as c:
        loose = c.execute(
            "SELECT DISTINCT release_mbid FROM files WHERE artist_mbid = ? AND rg_mbid IS NULL AND release_mbid IS NOT NULL",
            (mbid,)).fetchall()
    for r in loose:
        rg = mb.get_release_group_of_release(r["release_mbid"])
        if rg:
            with db.conn() as c:
                c.execute("UPDATE files SET rg_mbid = ? WHERE release_mbid = ? AND rg_mbid IS NULL",
                          (rg["id"], r["release_mbid"]))

    with db.conn() as c:
        owned = [r["rg_mbid"] for r in c.execute(
            "SELECT DISTINCT rg_mbid FROM files WHERE artist_mbid = ? AND rg_mbid IS NOT NULL", (mbid,))]
        known = {r["mbid"] for r in c.execute("SELECT mbid FROM release_groups WHERE artist_mbid = ?", (mbid,))}
    for rg_id in owned:
        if rg_id not in known:  # e.g. compilation credited to another artist
            rg = mb.get_release_group(rg_id)
            if rg:
                with db.conn() as c:
                    _upsert_rgs(c, mbid, [rg])
        ensure_tracks(rg_id)
    db.bump_version()


def fill_counts(mbid: str, settings: dict) -> None:
    """Phase B: choose a representative release (-> track count) for wanted groups the user lacks."""
    with db.conn() as c:
        rows = c.execute(
            """SELECT g.mbid, g.primary_type, g.secondary_types FROM release_groups g
               LEFT JOIN rg_state s ON s.rg_mbid = g.mbid
               WHERE g.artist_mbid = ? AND s.rg_mbid IS NULL""", (mbid,)).fetchall()
    for r in rows:
        sec = [s for s in (r["secondary_types"] or "").split(",") if s]
        if is_wanted(r["primary_type"], sec, settings):
            _store_release_choice(r["mbid"])
    with db.conn() as c:
        c.execute("UPDATE artists SET counts_at = ? WHERE mbid = ?", (time.time(), mbid))


def pending_artists(force_all=False) -> tuple[list[str], list[str]]:
    cutoff = time.time() - config.REFRESH_DAYS * 86400
    with db.conn() as c:
        lib = [r["artist_mbid"] for r in c.execute(
            "SELECT DISTINCT artist_mbid FROM files WHERE artist_mbid IS NOT NULL")]
        info = {r["mbid"]: r for r in c.execute("SELECT mbid, synced_at, counts_at FROM artists")}
    a = [m for m in lib if force_all or not info.get(m) or not info[m]["synced_at"] or info[m]["synced_at"] < cutoff]
    b = [m for m in lib if force_all or not info.get(m) or not info[m]["counts_at"] or info[m]["counts_at"] < cutoff]
    return a, b


def sync_all(progress: dict, force_all=False) -> None:
    settings = db.get_settings()
    a, b = pending_artists(force_all)
    total = len(a) + len(b)
    progress.update(phase="syncing", done=0, total=total, message=f"Syncing {len(a)} artists")
    n = 0
    for m in a:
        progress["message"] = f"Fetching artist {m}"
        try:
            sync_artist(m)
        except Exception:
            log.exception("sync failed for %s", m)
        n += 1
        progress["done"] = n
        _name(progress, m)
    for m in b:
        try:
            fill_counts(m, settings)
        except Exception:
            log.exception("count fill failed for %s", m)
        n += 1
        progress["done"] = n
        _name(progress, m, "Counting missing albums for")
    db.bump_version()
    progress["message"] = "Sync done"


def _name(progress, mbid, prefix="Synced"):
    with db.conn() as c:
        r = c.execute("SELECT name FROM artists WHERE mbid = ?", (mbid,)).fetchone()
    progress["message"] = f"{prefix} {r['name'] if r else mbid}"


def sync_one(progress: dict, mbid: str) -> None:
    progress.update(phase="syncing", done=0, total=2, message=f"Refreshing {mbid}")
    with db.conn() as c:  # forget cached choices so they are recomputed
        c.execute("DELETE FROM rg_state WHERE rg_mbid IN (SELECT mbid FROM release_groups WHERE artist_mbid = ?)", (mbid,))
    sync_artist(mbid)
    progress["done"] = 1
    fill_counts(mbid, db.get_settings())
    progress.update(done=2, message="Sync done")
