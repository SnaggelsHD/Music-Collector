# Music Collector

A lightweight, Lidarr-style library viewer that shows **what you have and what's missing** for every artist in
your music collection. It reads the MusicBrainz tags written by [Picard](https://picard.musicbrainz.org/) from your
files (no particular folder structure needed), fetches each artist's discography from MusicBrainz, and shows a
completion percentage per artist and per album, with covers and track lists.

[![Docker image](https://github.com/SnaggelsHD/Music-Collector/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/SnaggelsHD/Music-Collector/actions/workflows/docker-publish.yml)

## Run it

A prebuilt multi-arch image (amd64 + arm64) is published to GitHub Container Registry:

```bash
docker run -d --name music-collector -p 8080:8080 \
  -e MB_CONTACT="you@example.com" \
  -v /path/to/your/music:/music:ro \
  -v ./config:/config \
  ghcr.io/snaggelshd/music-collector:latest
```

Or with Compose: edit `docker-compose.yml` (point the `/music` volume at your NAS share, set `MB_CONTACT`), then:

```bash
docker compose up -d
```

(Use `docker compose up -d --build` to build from source instead.) Open http://localhost:8080. The first scan
starts automatically.

| Env var | Default | Meaning |
|---|---|---|
| `MB_CONTACT` | repo URL | Contact (email/URL) added to the MusicBrainz User-Agent — please set it |
| `SCAN_ON_START` | `true` | Scan + sync when the container starts |
| `SCAN_INTERVAL_HOURS` | `0` | Periodic rescan (0 = manual only) |
| `REFRESH_DAYS` | `30` | Re-fetch artist metadata after N days |

`/config` holds the SQLite database and the cover cache; mount it to keep data across restarts.

## Multiple libraries

Every folder directly below `/music` is treated as a separate library. Mount your parent folder (e.g.
`/volume4/Music:/music:ro`) or several volumes (`/a:/music/A:ro`, `/b:/music/B:ro`) and pick a library in the
sidebar — or "All libraries" for the combined view. Completion is calculated per selected library. See
`docker-compose.nas.yml` for a Synology-style example.

## How it works

1. **Scan** – walks `/music`, reads tags with mutagen (MP3, FLAC, M4A, Ogg/Opus, WMA, APE, …). Only new/changed
   files are re-read. Uses Picard's `MusicBrainz Album Artist Id` (falling back to `Artist Id`), `Release Group Id`,
   `Release Id`, `Recording Id` and `Release Track Id`.
2. **Sync** – per artist: artist info + all release groups from MusicBrainz, then the track list of the release you
   own (or of the most standard release for albums you don't have). MusicBrainz allows 1 request/second, so the
   first sync of a big library takes a while; results are cached and the UI fills in as it progresses.
3. **Completion** – tracks are matched by recording ID / release track ID (title fallback for files without
   recording IDs). Artist % = owned tracks ÷ total tracks over all *wanted* albums; albums you own always count.
4. **Covers** – from the Cover Art Archive (cached on disk), falling back to `cover.jpg`/`folder.jpg` in the album folder.
   MusicBrainz has no artist photos, so the artist poster is the cover of your newest album.

Which release types count (Album/EP/Single, and Studio/Live/Compilation/…) is configurable under **Settings**;
the default mirrors Lidarr's "studio albums, EPs and singles".

## Notes

- Mount the music read-only (`:ro`); the app never writes to it.
- "Various Artists" compilations show only what you own (their full discography is not fetched).
- Files without MusicBrainz tags are counted as "untagged" and not listed; run them through Picard.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
MUSIC_DIR=/path/to/music CONFIG_DIR=./config uvicorn app.main:app --reload --port 8080
```
