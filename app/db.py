import json
import sqlite3
import threading
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    mtime REAL, size INTEGER, codec TEXT, bitrate INTEGER, duration REAL,
    title TEXT, album TEXT, artist TEXT, albumartist TEXT,
    track_no INTEGER, disc_no INTEGER,
    artist_mbid TEXT,          -- library artist (album artist, falls back to track artist)
    rg_mbid TEXT, release_mbid TEXT, recording_mbid TEXT, track_mbid TEXT,
    library TEXT               -- first folder below the music root
);
CREATE INDEX IF NOT EXISTS idx_files_artist ON files(artist_mbid);
CREATE INDEX IF NOT EXISTS idx_files_rg ON files(rg_mbid);

CREATE TABLE IF NOT EXISTS artists (
    mbid TEXT PRIMARY KEY, name TEXT, sort_name TEXT, disambiguation TEXT,
    type TEXT, country TEXT, begin TEXT, end TEXT,
    synced_at REAL, counts_at REAL
);
CREATE TABLE IF NOT EXISTS release_groups (
    mbid TEXT, artist_mbid TEXT, title TEXT,
    primary_type TEXT, secondary_types TEXT, first_release_date TEXT,
    PRIMARY KEY (mbid, artist_mbid)
);
CREATE INDEX IF NOT EXISTS idx_rg_artist ON release_groups(artist_mbid);

CREATE TABLE IF NOT EXISTS rg_state (       -- chosen representative release per release group
    rg_mbid TEXT PRIMARY KEY, release_mbid TEXT, track_count INTEGER,
    tracks_fetched INTEGER DEFAULT 0, fetched_at REAL
);
CREATE TABLE IF NOT EXISTS tracks (
    release_mbid TEXT, disc INTEGER, position INTEGER,
    track_mbid TEXT, recording_mbid TEXT, title TEXT, length_ms INTEGER,
    PRIMARY KEY (release_mbid, disc, position)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_SETTINGS = {
    "primary_types": ["Album", "EP", "Single"],
    # "Studio" = no secondary type. Everything else must be listed to be wanted.
    "secondary_types": ["Studio"],
}

_data_version = 0
_lock = threading.Lock()


def bump_version():
    global _data_version
    _data_version += 1


def data_version() -> int:
    return _data_version


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.COVER_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(files)")]
        if "library" not in cols:  # migrate databases created before multi-library support
            c.execute("ALTER TABLE files ADD COLUMN library TEXT")
            c.execute("UPDATE files SET library = CASE WHEN instr(path, '/') > 0 "
                      "THEN substr(path, 1, instr(path, '/') - 1) ELSE '(root)' END")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_library ON files(library)")


def get_settings() -> dict:
    with conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    for r in rows:
        s[r["key"]] = json.loads(r["value"])
    return s


def save_settings(new: dict):
    with conn() as c:
        for k in DEFAULT_SETTINGS:
            if k in new:
                c.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (k, json.dumps(new[k])))
        c.execute("UPDATE artists SET counts_at = NULL")  # newly wanted types need track counts
    bump_version()
