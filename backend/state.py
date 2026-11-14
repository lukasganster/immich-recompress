"""Shared in-memory state for the encode queue plus the cancellation
primitives.

Shared containers are mutated in place so all modules observe the same state.
`_scheduler.condition` protects task admission, asset claims and disk budgets;
_state_lock protects job records. Scheduler callbacks run outside its condition.
"""
import threading
from itertools import count
from uuid import uuid4


class SchedulerState:
    """Everything the job scheduler owns. Guarded by `condition`."""

    def __init__(self):
        self.condition = threading.Condition()
        self.pending = []  # tasks waiting for a slot, in submission order
        self.active = {}  # job_id -> task holding a processing slot
        self.assets = {}  # underlying asset_id -> owning job_id (also in review)
        self.asset_versions = {}  # asset_id -> version when its owner released it
        self.version = 0  # bumped on each release, invalidates prepared metadata
        self.waiting_on = {}  # job_id -> job_id holding a related asset it needs
        self.concurrent_jobs = 1
        self.settings_lock = threading.Lock()  # serialises persisting the limit
        self.started = threading.Event()
        self.sequence = count()


# --- job / queue state ---
_jobs = {}                      # asset_id -> job dict
_queue_order = []               # list of asset_ids waiting / known
_state_lock = threading.Lock()
_scheduler = SchedulerState()
_job_updates = count(1)
_server_session = uuid4().hex
_csv_lock = threading.Lock()


# --- per-asset API key map (asset_id -> key index) ---
_asset_key_map: dict = {}
_asset_key_lock = threading.Lock()

# --- browse metadata discovery cache ---
_discovery_cache = {}
_discovery_lock = threading.Lock()

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
