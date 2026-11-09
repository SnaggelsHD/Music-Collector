"""Walks the music directory and reads Picard/MusicBrainz tags with mutagen."""
import logging
import os
import re
from pathlib import Path

import mutagen
from mutagen.id3 import ID3

from . import config, db

log = logging.getLogger("scanner")

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# Normalised key names (lowercase alphanumerics only) for the different tag formats.
BASIC = {
    "title": ["title", "tit2", "nam", "wmtitle"],
    "album": ["album", "talb", "alb", "wmalbumtitle"],
    "artist": ["artist", "tpe1", "art", "author"],
    "albumartist": ["albumartist", "tpe2", "aart", "wmalbumartist"],
    "track": ["tracknumber", "track", "trck", "trkn", "wmtracknumber"],
    "disc": ["discnumber", "disc", "tpos", "disk", "wmpartofset"],
}
MBIDS = {
    "artist_mbid": ["musicbrainzartistid"],
    "albumartist_mbid": ["musicbrainzalbumartistid"],
    "rg_mbid": ["musicbrainzreleasegroupid"],
    "release_mbid": ["musicbrainzalbumid", "musicbrainzreleaseid"],
    "track_mbid": ["musicbrainzreleasetrackid"],
    "recording_mbid": ["musicbrainztrackid", "ufidmusicbrainz"],
}


def _norm(key) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _to_strs(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        if v and all(isinstance(x, int) for x in v):  # MP4 trkn/disk: (n, total)
            return [f"{v[0]}/{v[1]}" if len(v) > 1 and v[1] else str(v[0])]
        out = []
        for x in v:
            out.extend(_to_strs(x))
        return out
    if isinstance(v, bytes):
        return [v.decode("utf-8", "ignore").strip("\x00")]
    if hasattr(v, "text"):
        return _to_strs(v.text)
    if hasattr(v, "value"):
        return _to_strs(v.value)
    return [str(v)]


def _flatten_tags(audio) -> dict[str, list[str]]:
    flat: dict[str, list[str]] = {}
    tags = audio.tags
    if tags is None:
        return flat

    def add(key, values):
        vals = [x for x in values if x]
        if vals:
            flat.setdefault(_norm(key), []).extend(vals)

    if isinstance(tags, ID3):
        for frame in tags.values():
            fid = frame.FrameID
            if fid == "TXXX":
                add(frame.desc, _to_strs(frame.text))
            elif fid == "UFID":
                if frame.owner == "http://musicbrainz.org":
                    add("ufidmusicbrainz", _to_strs(frame.data))
            elif fid.startswith("T"):
                add(fid, _to_strs(frame.text))
    else:
        try:
            items = list(tags.items())
        except Exception:
            items = []
        for key, value in items:
            name = str(key)
            if name.startswith("----:"):  # MP4 freeform atoms: ----:com.apple.iTunes:Name
                name = name.split(":")[-1]
            add(name, _to_strs(value))
    return flat


def _first(flat, keys):
    for k in keys:
        if flat.get(k):
            return flat[k][0]
    return None


def _first_uuid(flat, keys):
    for k in keys:
        for v in flat.get(k, []):
            m = UUID_RE.search(v)
            if m:
                return m.group(0).lower()
    return None


def _int(s):
    if not s:
        return None
    m = re.match(r"\s*(\d+)", str(s))
    return int(m.group(1)) if m else None


def read_file(path: Path) -> dict | None:
    try:
        audio = mutagen.File(path)
    except Exception as e:  # corrupt files shouldn't kill the scan
        log.warning("cannot read %s: %s", path, e)
        return None
    if audio is None:
        return None
    flat = _flatten_tags(audio)
    rec = {k: _first(flat, v) for k, v in BASIC.items()}
    for k, v in MBIDS.items():
        rec[k] = _first_uuid(flat, v)
    info = audio.info
    rec["bitrate"] = int(getattr(info, "bitrate", 0) or 0) // 1000 or None
    rec["duration"] = float(getattr(info, "length", 0) or 0) or None
    rec["codec"] = path.suffix.lower().lstrip(".")
    # Library artist: prefer album artist, fall back to track artist.
    rec["lib_artist_mbid"] = rec["albumartist_mbid"] or rec["artist_mbid"]
    rec["lib_artist_name"] = rec["albumartist"] or rec["artist"]
    return rec


def iter_audio_files(root: Path):
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in filenames:
            if Path(fn).suffix.lower() in config.AUDIO_EXTS and not fn.startswith("._"):
                yield Path(dirpath) / fn


def scan(progress: dict) -> None:
    """Incremental scan. `progress` is a dict updated for the UI."""
    root = config.MUSIC_DIR
    progress.update(phase="scanning", done=0, total=0, message=f"Listing {root}")
    if not root.exists():
        raise RuntimeError(f"Music directory {root} does not exist")

    with db.conn() as c:
        known = {r["path"]: (r["mtime"], r["size"]) for r in c.execute("SELECT path, mtime, size FROM files")}

    seen: set[str] = set()
    todo: list[tuple[Path, float, int]] = []
    for p in iter_audio_files(root):
        rel = p.relative_to(root).as_posix()
        seen.add(rel)
        try:
            st = p.stat()
        except OSError:
            continue
        if known.get(rel) != (st.st_mtime, st.st_size):
            todo.append((p, st.st_mtime, st.st_size))

    progress.update(total=len(todo), message=f"{len(todo)} new/changed files of {len(seen)}")
    batch = []
    for i, (p, mtime, size) in enumerate(todo, 1):
        rec = read_file(p)
        rel = p.relative_to(root).as_posix()
        if rec:
            batch.append((
                rel, mtime, size, rec["codec"], rec["bitrate"], rec["duration"],
                rec["title"], rec["album"], rec["artist"], rec["albumartist"],
                _int(rec["track"]), _int(rec["disc"]),
                rec["lib_artist_mbid"], rec["rg_mbid"], rec["release_mbid"],
                rec["recording_mbid"], rec["track_mbid"], rec["lib_artist_name"],
            ))
        progress["done"] = i
        if len(batch) >= 200 or i == len(todo):
            _flush(batch)
            batch = []

    removed = set(known) - seen
    with db.conn() as c:
        c.executemany("DELETE FROM files WHERE path = ?", [(r,) for r in removed])
    db.bump_version()
    progress.update(message=f"Scan done: {len(todo)} updated, {len(removed)} removed")


def _flush(batch):
    if not batch:
        return
    with db.conn() as c:
        for row in batch:
            c.execute(
                """INSERT OR REPLACE INTO files
                   (path, mtime, size, codec, bitrate, duration, title, album, artist, albumartist,
                    track_no, disc_no, artist_mbid, rg_mbid, release_mbid, recording_mbid, track_mbid)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row[:17])
            # remember the artist name for artists we never get from MusicBrainz
            if row[12] and row[17]:
                c.execute("INSERT OR IGNORE INTO artists (mbid, name, sort_name) VALUES (?,?,?)",
                          (row[12], row[17], row[17]))
