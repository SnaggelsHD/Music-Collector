"""Single background worker so scans and MusicBrainz syncs never run concurrently."""
import logging
import queue
import threading
import time

from . import scanner, sync

log = logging.getLogger("jobs")

_q: queue.Queue = queue.Queue()
state = {"running": False, "job": None, "phase": "idle", "done": 0, "total": 0,
         "message": "", "error": None, "queued": 0, "finished_at": None}
_pending: set = set()


def submit(kind: str, arg: str | None = None) -> bool:
    key = (kind, arg)
    if key in _pending:
        return False
    _pending.add(key)
    _q.put(key)
    state["queued"] = len(_pending)
    return True


def _run(kind, arg):
    if kind == "scan":
        scanner.scan(state)
    elif kind == "sync":
        sync.sync_all(state)
    elif kind == "sync_force":
        sync.sync_all(state, force_all=True)
    elif kind == "artist":
        sync.sync_one(state, arg)


def _worker():
    while True:
        kind, arg = _q.get()
        state.update(running=True, job=kind, error=None, done=0, total=0, message="Starting")
        try:
            _run(kind, arg)
        except Exception as e:
            log.exception("job %s failed", kind)
            state["error"] = str(e)
        finally:
            _pending.discard((kind, arg))
            state.update(running=bool(_pending), phase="idle", queued=len(_pending), finished_at=time.time())


def start():
    threading.Thread(target=_worker, daemon=True, name="worker").start()


def scan_and_sync():
    submit("scan")
    submit("sync")
