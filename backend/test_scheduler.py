"""Concurrent scheduling with real threads and controlled work and filesystems."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import scheduler
from backend.config import MIN_FREE_BYTES
from backend.state import _scheduler


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        with _scheduler.condition:
            _scheduler.active.clear()
            _scheduler.pending.clear()
            _scheduler.assets.clear()
            _scheduler.concurrent_jobs = 1
        self.stop = threading.Event()
        self.release = threading.Event()
        self.errors = []
        self.events = []
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.thread = threading.Thread(
            target=scheduler.run_scheduler, args=(self.stop,)
        )
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.release.set()
        with _scheduler.condition:
            _scheduler.condition.wait_for(lambda: not _scheduler.active, timeout=2)
        self.stop.set()
        scheduler.wake()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        with _scheduler.condition:
            _scheduler.pending.clear()
            _scheduler.assets.clear()

    def task(self, name, started=None, *, priority=False, assets=(), disk=(), run=None):
        def work():
            if started:
                started.set()
            if run:
                return run()
            self.release.wait(timeout=3)
            return False

        task = scheduler.Task(
            name,
            prepare=lambda: scheduler.PreparedWork(
                work,
                frozenset(assets),
                tuple(scheduler.DiskNeed(d, amount, f"{name}_") for d, amount in disk),
            ),
            notify=lambda event: self.events.append((name, event)),
            failed=self.errors.append,
            priority=priority,
        )
        scheduler.submit(task)
        return task

    def wait_idle(self):
        with _scheduler.condition:
            self.assertTrue(
                _scheduler.condition.wait_for(
                    lambda: not _scheduler.active and not _scheduler.pending, timeout=2
                )
            )

    def test_default_serializes_and_increasing_limit_starts_more_work(self):
        first, second = threading.Event(), threading.Event()
        self.task("first", first)
        self.task("second", second)
        self.assertTrue(first.wait(1))
        self.assertFalse(second.wait(0.05))
        scheduler.set_concurrency(2)
        self.assertTrue(second.wait(1))
        self.assertEqual(set(scheduler.active_jobs()), {"first", "second"})
        self.release.set()
        self.wait_idle()

    def test_lowering_limit_lets_running_jobs_finish_before_starting_more(self):
        scheduler.set_concurrency(2)
        first, second, third = (threading.Event() for _ in range(3))
        finish_first, finish_second = threading.Event(), threading.Event()
        self.addCleanup(finish_first.set)
        self.addCleanup(finish_second.set)
        self.task("first", first, run=lambda: finish_first.wait(2) and False)
        self.task("second", second, run=lambda: finish_second.wait(2) and False)
        self.task("third", third)
        self.assertTrue(first.wait(1))
        self.assertTrue(second.wait(1))
        scheduler.set_concurrency(1)
        finish_first.set()
        self.assertFalse(third.wait(0.05))
        finish_second.set()
        self.assertTrue(third.wait(1))
        self.release.set()
        self.wait_idle()

    def test_confirmations_take_next_slot_ahead_of_fifo_ordinary_jobs(self):
        started = threading.Event()
        order = []
        self.task("running", started)
        self.assertTrue(started.wait(1))
        for name, priority in [
            ("first", False),
            ("second", False),
            ("confirmed", True),
        ]:
            self.task(name, priority=priority, run=lambda n=name: order.append(n))
        self.release.set()
        self.wait_idle()
        self.assertEqual(order, ["confirmed", "first", "second"])

    def test_failure_releases_slot_and_does_not_stop_later_jobs(self):
        def fail():
            raise RuntimeError("encoder failed")

        later = threading.Event()
        self.task("broken", run=fail)
        self.task("later", later, run=lambda: False)
        self.assertTrue(later.wait(1))
        self.wait_idle()
        self.assertEqual(str(self.errors[0]), "encoder failed")

    def test_review_releases_slot_but_keeps_related_assets_until_discarded(self):
        reviewed, unrelated, linked = (threading.Event() for _ in range(3))
        self.task("still", reviewed, assets=("still", "clip"), run=lambda: True)
        self.assertTrue(reviewed.wait(1))
        self.wait_idle()
        self.task("other", unrelated, assets=("other",), run=lambda: False)
        self.task("clip", linked, assets=("clip",), run=lambda: False)
        self.assertTrue(unrelated.wait(1))
        self.assertFalse(linked.wait(0.05))
        scheduler.release_assets("still")
        self.assertTrue(linked.wait(1))
        self.wait_idle()

    def test_confirmation_reuses_its_review_asset_ownership(self):
        self.task("still", assets=("still", "clip"), run=lambda: True)
        self.wait_idle()
        confirmed = threading.Event()
        self.task(
            "still",
            confirmed,
            priority=True,
            assets=("still", "clip"),
            run=lambda: False,
        )
        self.assertTrue(confirmed.wait(1))
        self.wait_idle()
        self.assertEqual(_scheduler.assets, {})

    def test_disk_reservations_block_oversubscription_and_resume_automatically(self):
        directory = self.directory.name
        budget = 1000
        first, second = threading.Event(), threading.Event()
        with patch.object(scheduler.shutil, "disk_usage") as usage:
            usage.return_value.free = MIN_FREE_BYTES + 1500
            scheduler.set_concurrency(2)
            self.task("first", first, disk=((directory, budget),))
            self.task("second", second, disk=((directory, budget),))
            self.assertTrue(first.wait(1))
            self.assertFalse(second.wait(0.05))
            self.assertIn(("second", "Waiting for disk space"), self.events)
            self.release.set()
            self.assertTrue(second.wait(1))
            self.wait_idle()

    def test_same_device_directories_share_one_budget(self):
        directory = Path(self.directory.name)
        backup = directory / "backup"
        backup.mkdir()
        started = threading.Event()
        with patch.object(scheduler.shutil, "disk_usage") as usage:
            usage.return_value.free = MIN_FREE_BYTES + 1500
            self.task(
                "large", started, disk=((str(directory), 1000), (str(backup), 1000))
            )
            self.assertFalse(started.wait(0.05))
            usage.return_value.free = MIN_FREE_BYTES + 2500
            self.assertTrue(started.wait(1))
            self.release.set()
            self.wait_idle()

    def test_cancelled_waiting_job_does_not_need_disk_space_to_finish(self):
        cancelled = threading.Event()
        finished = threading.Event()
        with patch.object(scheduler.shutil, "disk_usage") as usage:
            usage.return_value.free = 0
            task = self.task("cancelled", disk=((self.directory.name, 1000),))
            task.cancelled = cancelled.is_set
            original_notify = task.notify

            def notify(event):
                original_notify(event)
                if event == "finished":
                    finished.set()

            task.notify = notify
            cancelled.set()
            scheduler.wake()
            self.assertTrue(finished.wait(1))
            self.wait_idle()

    def test_invalid_limits_are_rejected(self):
        for value in [0, 9, True, 1.5, "2", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                scheduler.set_concurrency(value)
        self.assertEqual(scheduler.concurrency(), 1)

    def test_confirmation_can_queue_during_original_slot_cleanup(self):
        scheduler.set_concurrency(2)
        original_started, confirmed_started, finish_original = (
            threading.Event() for _ in range(3)
        )
        self.addCleanup(finish_original.set)
        self.task(
            "same",
            original_started,
            assets=("same", "clip"),
            run=lambda: finish_original.wait(2) and True,
        )
        self.assertTrue(original_started.wait(1))
        self.task(
            "same",
            confirmed_started,
            priority=True,
            assets=("same", "clip"),
            run=lambda: False,
        )
        self.assertFalse(confirmed_started.wait(0.05))
        self.assertEqual(scheduler.active_jobs(), ["same"])
        finish_original.set()
        self.assertTrue(confirmed_started.wait(1))
        self.wait_idle()

    def test_related_asset_metadata_is_refreshed_after_ownership_is_released(self):
        self.task("still", assets=("still", "clip"), run=lambda: True)
        self.wait_idle()
        started = threading.Event()
        prepared = scheduler.PreparedWork(run=lambda: started.set() and False, assets=frozenset({'clip'}))
        prepare = Mock(return_value=prepared)
        scheduler.submit(scheduler.Task('clip', prepare, lambda _: None, self.errors.append))
        self.assertFalse(started.wait(0.05))
        scheduler.release_assets("still")
        self.assertTrue(started.wait(1))
        self.wait_idle()
        self.assertEqual(prepare.call_count, 2)

    def test_prepare_failure_releases_the_slot(self):
        started = threading.Event()
        task = scheduler.Task(
            "broken",
            prepare=lambda: (_ for _ in ()).throw(RuntimeError("metadata failed")),
            notify=lambda _: None,
            failed=self.errors.append,
        )
        scheduler.submit(task)
        self.task("next", started, run=lambda: False)
        self.assertTrue(started.wait(1))
        self.wait_idle()
        self.assertEqual(str(self.errors[0]), "metadata failed")

    def test_slow_metadata_does_not_block_other_jobs_from_starting(self):
        scheduler.set_concurrency(2)
        barrier = threading.Barrier(2)
        first, second = threading.Event(), threading.Event()
        for name, started in [("first", first), ("second", second)]:

            def prepare(event=started):
                barrier.wait(timeout=1)
                return scheduler.PreparedWork(run=lambda: event.set() and False)

            scheduler.submit(
                scheduler.Task(name, prepare, lambda _: None, self.errors.append)
            )
        self.assertTrue(first.wait(2))
        self.assertTrue(second.wait(2))
        self.wait_idle()
        self.assertEqual(self.errors, [])

    def test_related_asset_wait_does_not_starve_unrelated_work(self):
        self.task("review", assets=("clip",), run=lambda: True)
        self.wait_idle()
        blocked, other = threading.Event(), threading.Event()
        self.task("clip", blocked, assets=("clip",), run=lambda: False)
        self.task("other", other, assets=("other",), run=lambda: False)
        self.assertTrue(other.wait(1))
        self.assertFalse(blocked.is_set())
        scheduler.release_assets("review")
        self.assertTrue(blocked.wait(1))
        self.wait_idle()

    def test_disk_wait_keeps_later_ordinary_jobs_in_submission_order(self):
        large, small = threading.Event(), threading.Event()
        with patch.object(scheduler.shutil, "disk_usage") as usage:
            usage.return_value.free = MIN_FREE_BYTES + 1500
            self.task(
                "large", large, disk=((self.directory.name, 2000),), run=lambda: False
            )
            self.task(
                "small", small, disk=((self.directory.name, 100),), run=lambda: False
            )
            self.assertFalse(small.wait(0.3))
            self.assertFalse(large.is_set())
            usage.return_value.free = MIN_FREE_BYTES + 2500
            scheduler.wake()
            self.assertTrue(large.wait(1))
            self.assertTrue(small.wait(1))
            self.wait_idle()

    def test_confirmations_are_not_held_behind_a_disk_wait(self):
        large, confirmed = threading.Event(), threading.Event()
        with patch.object(scheduler.shutil, "disk_usage") as usage:
            usage.return_value.free = MIN_FREE_BYTES + 100
            self.task(
                "large", large, disk=((self.directory.name, 2000),), run=lambda: False
            )
            self.assertTrue(self._waits_for_disk("large"))
            self.task("confirmed", confirmed, priority=True, run=lambda: False)
            self.assertTrue(confirmed.wait(1))
            self.assertFalse(large.is_set())
            usage.return_value.free = MIN_FREE_BYTES + 2500
            self.assertTrue(large.wait(1))
            self.wait_idle()

    def test_bytes_a_running_job_wrote_are_not_reserved_twice(self):
        directory = Path(self.directory.name)
        scheduler.set_concurrency(2)
        first, second, wrote = threading.Event(), threading.Event(), threading.Event()
        finish_first = threading.Event()
        self.addCleanup(finish_first.set)

        def write_then_wait():
            (directory / "first_src.mov").write_bytes(b"x" * 800)
            wrote.set()
            finish_first.wait(2)
            return False

        with patch.object(scheduler.shutil, "disk_usage") as usage:
            usage.return_value.free = MIN_FREE_BYTES + 1500
            self.task("first", first, disk=((str(directory), 1000),), run=write_then_wait)
            self.assertTrue(wrote.wait(1))
            # Live free space already reflects the 800 bytes first wrote.
            usage.return_value.free = MIN_FREE_BYTES + 700
            self.task("second", second, disk=((str(directory), 1000),), run=lambda: False)
            self.assertFalse(second.wait(0.3))
            # Room for second plus the 200 bytes first still owes, but not for
            # first's full 1000-byte estimate on top.
            usage.return_value.free = MIN_FREE_BYTES + 1300
            self.assertTrue(second.wait(1))
            finish_first.set()
            self.wait_idle()

    def test_related_asset_wait_names_the_blocking_job(self):
        self.task("review", assets=("clip",), run=lambda: True)
        self.wait_idle()
        self.task("clip", assets=("clip",), run=lambda: False)
        with _scheduler.condition:
            self.assertTrue(
                _scheduler.condition.wait_for(
                    lambda: _scheduler.waiting_on.get("clip") == "review", timeout=1
                )
            )
        self.assertEqual(scheduler.blocker("clip"), "review")
        scheduler.release_assets("review")
        self.wait_idle()
        self.assertIsNone(scheduler.blocker("clip"))

    def _waits_for_disk(self, name):
        with _scheduler.condition:
            return _scheduler.condition.wait_for(
                lambda: any(
                    t.job_id == name and t.wait_reason is scheduler.WaitReason.DISK
                    for t in _scheduler.pending
                ),
                timeout=1,
            )

    def test_release_during_preparation_invalidates_the_metadata_snapshot(self):
        scheduler.set_concurrency(2)
        owner_started, finish_owner, captured, return_metadata, ran = (
            threading.Event() for _ in range(5)
        )
        self.addCleanup(finish_owner.set)
        self.addCleanup(return_metadata.set)
        model = {"linked": "old"}
        observed = []

        def replace():
            finish_owner.wait(2)
            model["linked"] = "new"
            return False

        self.task("owner", owner_started, assets=("clip",), run=replace)
        self.assertTrue(owner_started.wait(1))
        preparations = []

        def prepare():
            linked = model["linked"]
            preparations.append(linked)
            if len(preparations) == 1:
                captured.set()
                return_metadata.wait(2)

            def run():
                observed.append(linked)
                ran.set()
                return False

            return scheduler.PreparedWork(run, frozenset({"clip"}))

        scheduler.submit(
            scheduler.Task("reader", prepare, lambda _: None, self.errors.append)
        )
        self.assertTrue(captured.wait(1))
        finish_owner.set()
        with _scheduler.condition:
            self.assertTrue(
                _scheduler.condition.wait_for(
                    lambda: "owner" not in _scheduler.active, timeout=1
                )
            )
        return_metadata.set()
        self.assertTrue(ran.wait(1))
        self.wait_idle()
        self.assertEqual(preparations, ["old", "new"])
        self.assertEqual(observed, ["new"])
