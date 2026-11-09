import os
from pathlib import Path

MUSIC_DIR = Path(os.environ.get("MUSIC_DIR", "/music"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
DB_PATH = CONFIG_DIR / "music-collector.db"
COVER_DIR = CONFIG_DIR / "covers"

# MusicBrainz asks for a meaningful User-Agent with contact info.
CONTACT = os.environ.get("MB_CONTACT", "https://github.com/SnaggelsHD/Music-Collector")
USER_AGENT = f"MusicCollector/1.0 ( {CONTACT} )"
MB_BASE = os.environ.get("MB_BASE", "https://musicbrainz.org/ws/2")
CAA_BASE = os.environ.get("CAA_BASE", "https://coverartarchive.org")
MB_MIN_INTERVAL = float(os.environ.get("MB_MIN_INTERVAL", "1.1"))  # seconds between requests

REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS", "30"))
SCAN_ON_START = os.environ.get("SCAN_ON_START", "true").lower() == "true"
SCAN_INTERVAL_HOURS = float(os.environ.get("SCAN_INTERVAL_HOURS", "0"))  # 0 = manual only

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus",
              ".wma", ".wv", ".ape", ".mpc", ".aiff", ".aif", ".wav"}

VARIOUS_ARTISTS = "89ad4ac3-39f7-470e-963a-56509c546377"
