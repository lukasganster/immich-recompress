"""Bounded job scheduling, asset ownership and disk admission in one process.

Every structure the scheduler touches lives on `_scheduler` in state.py and is
guarded by its condition. Task callbacks always run outside that condition, so
callers may take `_state_lock` first and then call into the scheduler.
"""

import os
import shutil
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, NamedTuple, Optional

from backend.config import MIN_FREE_BYTES
from backend.db import db_load_concurrency, db_save_concurrency
from backend.state import _scheduler

MAX_CONCURRENT_JOBS = 8


class WaitReason(str, Enum):
    ASSET = "Waiting for related asset"
    DISK = "Waiting for disk space"
    REFRESH = "Checking asset again"


class TaskEvent(str, Enum):
    """Lifecycle events a task reports through its `notify` callback."""

    PREPARING = "preparing"
    RUNNING = "running"
    CANCELLED = "cancelled"
    SETTLED = "settled"  # about to leave its slot; job state can be persisted
    FINISHED = "finished"  # slot released


class DiskNeed(NamedTuple):
    """Bytes a job may write into `directory`, in files named `prefix*`."""

    directory: str
    amount: int
    prefix: str


# True when the job ended in review and must keep its related-asset claims
# until the review is confirmed or discarded.
KeepClaims = bool


@dataclass
class PreparedWork:
    run: Callable[[], KeepClaims]
    assets: frozenset[str] = field(default_factory=frozenset)
    disk: tuple[DiskNeed, ...] = ()


def _owned_bytes(need: DiskNeed) -> int:
    total = 0
    try:
        with os.scandir(need.directory) as entries:
            for entry in entries:
                if entry.name.startswith(need.prefix) and entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
    except OSError:
        return 0
    return total


@dataclass
class DiskClaim:
    """An admitted job's budget on one directory, net of what it already wrote."""

    need: DiskNeed
    device: int
    baseline: int

    def remaining(self) -> int:
        written = max(0, _owned_bytes(self.need) - self.baseline)
        return max(0, self.need.amount - written)


@dataclass
class Task:
    job_id: str
    prepare: Callable[[], PreparedWork]
    notify: Callable[[object], None]
    failed: Callable[[Exception], None]
    cancelled: Callable[[], bool] = lambda: False
    priority: bool = False
    prepared: Optional[PreparedWork] = None
    claims: list[DiskClaim] = field(default_factory=list)
    wait_reason: Optional[WaitReason] = None
    notified_wait: Optional[tuple[WaitReason, Optional[str]]] = None
    generation: int = 0
    order: int = 0
    processing: bool = False


def concurrency() -> int:
    with _scheduler.condition:
        return _scheduler.concurrent_jobs


def _validate_concurrency(value: object) -> None:
    if type(value) is not int or not 1 <= value <= MAX_CONCURRENT_JOBS:
        raise ValueError(
            f"Concurrent jobs must be an integer from 1 to {MAX_CONCURRENT_JOBS}"
        )


def set_concurrency(value: int) -> None:
    _validate_concurrency(value)
    with _scheduler.condition:
        _scheduler.concurrent_jobs = value
        _scheduler.condition.notify_all()


def save_concurrency(value: int) -> None:
    """Validate, persist and apply a new limit. Raises ValueError or sqlite3.Error."""
    _validate_concurrency(value)
    with _scheduler.settings_lock:
        db_save_concurrency(value)
        set_concurrency(value)


def load_concurrency() -> None:
    set_concurrency(db_load_concurrency())


def active_jobs() -> list[str]:
    with _scheduler.condition:
        return [
            job_id for job_id, task in _scheduler.active.items() if task.processing
        ]


def has_task(job_id: str) -> bool:
    with _scheduler.condition:
        return job_id in _scheduler.active or any(
            task.job_id == job_id for task in _scheduler.pending
        )


def blocker(job_id: str) -> Optional[str]:
    """The job holding a related asset that `job_id` is waiting for, if any."""
    with _scheduler.condition:
        return _scheduler.waiting_on.get(job_id)


def submit(task: Task) -> None:
    with _scheduler.condition:
        active = _scheduler.active.get(task.job_id)
        if (active and (not task.priority or active.priority)) or any(
            item.job_id == task.job_id for item in _scheduler.pending
        ):
            raise ValueError("Job is already scheduled")
        task.order = next(_scheduler.sequence)
        _scheduler.pending.append(task)
        _scheduler.condition.notify_all()


def _release_assets(job_id: str) -> None:
    owned = [aid for aid, owner in _scheduler.assets.items() if owner == job_id]
    if owned:
        _scheduler.version += 1
        for asset_id in owned:
            del _scheduler.assets[asset_id]
            _scheduler.asset_versions[asset_id] = _scheduler.version


def release_assets(job_id: str) -> None:
    with _scheduler.condition:
        _release_assets(job_id)
        _scheduler.condition.notify_all()


def wake() -> None:
    with _scheduler.condition:
        _scheduler.condition.notify_all()


def cancel_pending(job_id: str) -> None:
    with _scheduler.condition:
        _scheduler.pending[:] = [
            task for task in _scheduler.pending if task.job_id != job_id
        ]
        _scheduler.waiting_on.pop(job_id, None)
        _scheduler.condition.notify_all()


def _reserve(task: Task) -> Optional[WaitReason]:
    """Called under the condition; filesystem budgets are shared per device."""
    work = task.prepared
    owner = None
    for asset_id in work.assets:
        holder = _scheduler.assets.get(asset_id)
        if holder is not None and holder != task.job_id:
            owner = holder
            break
    if owner:
        _scheduler.waiting_on[task.job_id] = owner
        return WaitReason.ASSET
    _scheduler.waiting_on.pop(task.job_id, None)
    if any(
        _scheduler.asset_versions.get(aid, 0) > task.generation for aid in work.assets
    ):
        return WaitReason.REFRESH
    needed: dict[int, int] = {}
    free: dict[int, int] = {}
    devices: list[int] = []
    for need in work.disk:
        device = os.stat(need.directory).st_dev
        devices.append(device)
        needed[device] = needed.get(device, 0) + max(0, need.amount)
        free[device] = shutil.disk_usage(need.directory).free
    for device, amount in needed.items():
        # Live free space already reflects what running jobs have written, so
        # only their unwritten remainder is still owed.
        owed = sum(
            claim.remaining()
            for item in _scheduler.active.values()
            if item is not task
            for claim in item.claims
            if claim.device == device
        )
        if free[device] < amount + owed + MIN_FREE_BYTES:
            return WaitReason.DISK
    task.claims = [
        DiskClaim(need, device, _owned_bytes(need))
        for need, device in zip(work.disk, devices)
    ]
    for asset_id in work.assets:
        _scheduler.assets[asset_id] = task.job_id
    return None


def _finish(task: Task, keep_assets: bool = False) -> None:
    try:
        task.notify(TaskEvent.SETTLED)
    finally:
        with _scheduler.condition:
            _scheduler.active.pop(task.job_id, None)
            _scheduler.waiting_on.pop(task.job_id, None)
            task.claims = []
            if not keep_assets:
                _release_assets(task.job_id)
            _scheduler.condition.notify_all()
    task.notify(TaskEvent.FINISHED)


def _run(task: Task) -> None:
    keep_assets = False
    try:
        with _scheduler.condition:
            task.processing = True
        task.notify(TaskEvent.RUNNING)
        keep_assets = bool(task.prepared.run())
    # Job code can fail in any way; the slot must still be released.
    except Exception as exc:
        task.failed(exc)
    finally:
        _finish(task, keep_assets)


def _defer(task: Task, reason: WaitReason) -> None:
    with _scheduler.condition:
        task.processing = False
        _scheduler.active.pop(task.job_id, None)
        if reason is WaitReason.REFRESH:
            task.prepared = None
        wait = (reason, _scheduler.waiting_on.get(task.job_id))
    # Rechecks run several times a second; only report what changed.
    if task.notified_wait != wait:
        task.notify(reason)
        task.notified_wait = wait
    with _scheduler.condition:
        task.wait_reason = reason
        _scheduler.pending.append(task)
        _scheduler.condition.notify_all()


def _prepare_and_run(task: Task) -> None:
    try:
        if task.cancelled():
            task.notify(TaskEvent.CANCELLED)
            _finish(task)
            return
        with _scheduler.condition:
            task.processing = True
            task.generation = _scheduler.version
        task.notify(TaskEvent.PREPARING)
        task.prepared = task.prepare()
        if task.cancelled():
            task.notify(TaskEvent.CANCELLED)
            _finish(task)
            return
        with _scheduler.condition:
            reason = _reserve(task)
        if reason:
            _defer(task, reason)
            return
    # Metadata lookups can fail in any way; report it and free the slot.
    except Exception as exc:
        try:
            task.failed(exc)
        finally:
            _finish(task)
        return
    _run(task)


def _eligible(attempted: set[int]) -> list[Task]:
    """Pending tasks that may start now, keeping ordinary jobs in FIFO order.

    An ordinary job waiting for disk space holds back every later ordinary job,
    so a large job is never overtaken indefinitely by smaller ones. Confirmed
    replacements and jobs waiting on a related asset are not held back.
    """
    barrier = min(
        (
            task.order
            for task in _scheduler.pending
            if not task.priority and task.wait_reason is WaitReason.DISK
        ),
        default=None,
    )
    return [
        task
        for task in _scheduler.pending
        if task.job_id not in _scheduler.active
        and task.order not in attempted
        and (task.priority or barrier is None or task.order <= barrier)
    ]


def run_scheduler(stop: Optional[threading.Event] = None) -> None:
    """Scan each candidate once, dispatching eligible jobs in priority/FIFO order."""
    attempted: set[int] = set()
    while stop is None or not stop.is_set():
        with _scheduler.condition:
            available = _eligible(attempted)
            if not available or len(_scheduler.active) >= _scheduler.concurrent_jobs:
                _scheduler.condition.wait(timeout=0.25)
                attempted.clear()
                continue
            task = min(available, key=lambda item: (not item.priority, item.order))
            _scheduler.pending.remove(task)
            _scheduler.active[task.job_id] = task
        try:
            # Callbacks acquire job state; enqueue takes the locks in reverse order.
            if task.cancelled():
                task.notify(TaskEvent.CANCELLED)
                _finish(task)
                continue
            reason = None
            if task.prepared:
                with _scheduler.condition:
                    reason = _reserve(task)
            if reason:
                attempted.add(task.order)
                _defer(task, reason)
                continue
            with _scheduler.condition:
                task.wait_reason = None
                task.notified_wait = None
            target = _run if task.prepared else _prepare_and_run
            threading.Thread(
                target=target, args=(task,), name=f"job-{task.job_id}", daemon=True
            ).start()
        # Never let one task's failure stop the scheduler loop.
        except Exception as exc:
            try:
                task.failed(exc)
            finally:
                _finish(task)


def start_scheduler() -> None:
    if not _scheduler.started.is_set():
        _scheduler.started.set()
        threading.Thread(
            target=run_scheduler, name="job-scheduler", daemon=True
        ).start()
