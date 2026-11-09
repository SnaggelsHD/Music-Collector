import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, covers, db, jobs, stats, sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


def _periodic():
    while config.SCAN_INTERVAL_HOURS > 0:
        time.sleep(config.SCAN_INTERVAL_HOURS * 3600)
        jobs.scan_and_sync()


@asynccontextmanager
async def lifespan(_app):
    db.init()
    jobs.start()
    if config.SCAN_ON_START:
        jobs.scan_and_sync()
    if config.SCAN_INTERVAL_HOURS > 0:
        threading.Thread(target=_periodic, daemon=True).start()
    yield


app = FastAPI(title="Music Collector", lifespan=lifespan)


@app.get("/api/status")
def status():
    snap = stats.snapshot()
    with db.conn() as c:
        libs = [dict(r) for r in c.execute(
            "SELECT library AS name, COUNT(*) AS files FROM files GROUP BY library ORDER BY library COLLATE NOCASE")]
    return {"job": jobs.state, "files": snap["files"], "unmatched": snap["unmatched"],
            "artists": len(snap["artists"]), "music_dir": str(config.MUSIC_DIR), "libraries": libs}


@app.post("/api/scan")
def scan(full: bool = False):
    jobs.submit("scan")
    jobs.submit("sync_force" if full else "sync")
    return {"ok": True}


@app.post("/api/artists/{mbid}/refresh")
def refresh_artist(mbid: str):
    jobs.submit("artist", mbid)
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.put("/api/settings")
def put_settings(body: dict):
    db.save_settings(body)
    return db.get_settings()


@app.get("/api/artists")
def artists(library: str | None = None):
    return list(stats.snapshot(library or None)["artists"].values())


def _artist_or_404(mbid, library=None):
    a = stats.snapshot(library)["artists"].get(mbid)
    if not a:
        raise HTTPException(404, "Artist not in library")
    return a


@app.get("/api/artists/{mbid}")
def artist(mbid: str, library: str | None = None):
    library = library or None
    snap = stats.snapshot(library)
    a = _artist_or_404(mbid, library)
    albums = sorted(snap["per_artist"].get(mbid, []), key=lambda s: s["date"] or "", reverse=True)
    return {**a, "albums": albums}


@app.get("/api/albums/{artist_mbid}/{rg_mbid}")
def album(artist_mbid: str, rg_mbid: str, library: str | None = None):
    library = library or None
    snap = stats.snapshot(library)
    s = snap["rg"].get((artist_mbid, rg_mbid))
    if not s:
        raise HTTPException(404, "Unknown album")
    try:
        sync.ensure_tracks(rg_mbid)  # lazy: fetch the track list if we don't have it yet
    except Exception as e:
        raise HTTPException(502, f"MusicBrainz error: {e}")
    with db.conn() as c:
        files = [dict(r) for r in c.execute(
            """SELECT path, title, codec, bitrate, duration, track_no, disc_no, recording_mbid, track_mbid
               FROM files WHERE rg_mbid = ? AND artist_mbid = ?""" + (" AND library = ?" if library else ""),
            (rg_mbid, artist_mbid) + ((library,) if library else ()))]
        st = c.execute("SELECT * FROM rg_state WHERE rg_mbid = ?", (rg_mbid,)).fetchone()
        tracks = [dict(r) for r in c.execute(
            "SELECT * FROM tracks WHERE release_mbid = ? ORDER BY disc, position",
            (st["release_mbid"] if st else None,))]
    rows, extra = stats.match_tracks(tracks, files)
    s = stats.snapshot(library)["rg"].get((artist_mbid, rg_mbid), s)
    return {**s, "release_mbid": st["release_mbid"] if st else None,
            "tracks": [{"disc": r["disc"], "position": r["position"], "title": r["title"],
                        "length_ms": r["length_ms"], "owned": bool(r["file"]),
                        "file": {k: r["file"][k] for k in ("path", "codec", "bitrate")} if r["file"] else None}
                       for r in rows],
            "extra_files": [{k: f[k] for k in ("path", "title", "codec", "bitrate")} for f in extra]}


@app.get("/api/cover/album/{rg_mbid}")
def cover_album(rg_mbid: str):
    res = covers.release_group_cover(rg_mbid)
    if not res:
        raise HTTPException(404)
    return Response(res[0], media_type=res[1], headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/cover/artist/{mbid}")
def cover_artist(mbid: str):
    albums = [s for s in stats.snapshot()["per_artist"].get(mbid, []) if s["in_library"]]
    albums.sort(key=lambda s: s["date"] or "", reverse=True)
    res = covers.artist_cover(mbid, [s["mbid"] for s in albums])
    if not res:
        raise HTTPException(404)
    return Response(res[0], media_type=res[1], headers={"Cache-Control": "public, max-age=86400"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
