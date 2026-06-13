"""Shared in-memory state for the encode queue plus the cancellation
primitives.

Kept in its own module so every other module reads/mutates the SAME objects
without import cycles. These containers are only ever *mutated* in place (never
reassigned), so `from backend.state import _jobs` shares the live object. The
one rebound value, the currently-encoding asset id, lives behind accessor
functions because a `from`-import of a rebound name would capture a stale value.
"""
import queue
import threading

# --- job / queue state ---
_jobs = {}                      # asset_id -> job dict
_queue_order = []               # list of asset_ids waiting / known
_state_lock = threading.Lock()
_work_queue = queue.Queue()     # asset_ids to process

_active_id = None               # asset_id currently encoding (or None)


def active_id():
    return _active_id


def set_active_id(value):
    global _active_id
    _active_id = value


# --- per-asset API key map (asset_id -> key index) ---
_asset_key_map: dict = {}
_asset_key_lock = threading.Lock()

# --- SSE subscribers ---
_subscribers = []
_sub_lock = threading.Lock()

# --- running encode subprocesses (asset_id -> Popen), for cancellation ---
_active_procs = {}
_proc_lock = threading.Lock()


def register_proc(asset_id, proc):
    with _proc_lock:
        _active_procs[asset_id] = proc


def unregister_proc(asset_id):
    with _proc_lock:
        _active_procs.pop(asset_id, None)


def kill_proc(asset_id):
    with _proc_lock:
        proc = _active_procs.get(asset_id)
    if proc:
        try:
            proc.kill()
        except OSError:
            pass


def is_cancelled(asset_id):
    with _state_lock:
        job = _jobs.get(asset_id)
        return bool(job and job.get("_cancel"))
