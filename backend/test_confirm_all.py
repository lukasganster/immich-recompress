"""Bulk replacement regressions: only listed reviews run, one at a time."""

import tempfile
import unittest
from pathlib import Path
import threading
from unittest.mock import patch

from flask import Flask

from backend import jobs, routes, scheduler
from backend.state import _jobs, _scheduler

FIRST = "30000000-0000-4000-8000-000000000001"
SECOND = "30000000-0000-4000-8000-000000000002"
THIRD = "30000000-0000-4000-8000-000000000003"
ENV = {"url": "https://immich.test", "api_keys": ["owner"], "api_key": "owner"}


def reviewed(asset_id, status="review"):
    return {"status": status, "media": "video", "name": f"{asset_id}.mov", "log": "",
            "progress": 100, "_key_idx": 0, "_out_path": f"/tmp/{asset_id}.mp4",
            "old_size": 100, "new_size": 40, "savings": 60, "codec": "h264"}


class ConfirmAllTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        _jobs.clear()
        self.addCleanup(_jobs.clear)
        self.order = []
        for name, value in (("get_env", ENV), ("emit_job_update", None),
                            ("broadcast_queue_update", None), ("persist_if_terminal", None)):
            patcher = patch.object(routes, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # A failed replacement is reported through jobs, which logs and persists.
        for name in (
            "persist_if_terminal",
            "emit_job_update",
            "broadcast_queue_update",
            "append_csv_log",
        ):
            patcher = patch.object(jobs, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        replace = patch.object(routes, "do_replace", side_effect=self.replace)
        replace.start()
        self.addCleanup(replace.stop)
        self.failing = None
        with _scheduler.condition:
            _scheduler.pending.clear()
            _scheduler.active.clear()
            _scheduler.assets.clear()
            _scheduler.concurrent_jobs = 1
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=scheduler.run_scheduler, args=(self.stop,)
        )
        self.thread.start()
        self.addCleanup(self.stop_worker)

    def stop_worker(self):
        self.wait_idle()
        self.stop.set()
        scheduler.wake()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def wait_idle(self):
        with _scheduler.condition:
            self.assertTrue(
                _scheduler.condition.wait_for(
                    lambda: not _scheduler.active and not _scheduler.pending, timeout=2
                )
            )

    def replace(self, _env, asset_id, *_args, **_kwargs):
        self.order.append(asset_id)
        if asset_id == self.failing:
            raise RuntimeError("upload died")
        jobs.update_job(asset_id, status="done")

    def confirm(self, ids):
        response = self.client.post("/api/confirm-all", json={"ids": ids})
        self.wait_idle()
        return response

    def confirm_one(self, asset_id):
        response = self.client.post(f"/api/confirm/{asset_id}")
        self.wait_idle()
        return response

    def test_listed_reviews_are_replaced_in_order(self):
        _jobs.update({FIRST: reviewed(FIRST), SECOND: reviewed(SECOND)})
        response = self.confirm([SECOND, FIRST])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["confirmed"], [SECOND, FIRST])
        self.assertEqual(self.order, [SECOND, FIRST])

    def test_a_review_the_user_did_not_list_is_left_alone(self):
        _jobs.update({FIRST: reviewed(FIRST), SECOND: reviewed(SECOND)})
        self.confirm([FIRST])
        self.assertEqual(self.order, [FIRST])
        self.assertEqual(_jobs[SECOND]["status"], "review")

    def test_jobs_not_in_review_and_bad_ids_are_skipped(self):
        _jobs.update({FIRST: reviewed(FIRST), SECOND: reviewed(SECOND, "encoding")})
        response = self.confirm([FIRST, SECOND, THIRD, "../etc", 7])
        self.assertEqual(response.json["confirmed"], [FIRST])
        self.assertEqual(response.json["skipped"], [SECOND, THIRD, "../etc", 7])
        self.assertEqual(self.order, [FIRST])

    def test_a_repeated_id_is_replaced_once(self):
        _jobs[FIRST] = reviewed(FIRST)
        self.confirm([FIRST, FIRST])
        self.assertEqual(self.order, [FIRST])

    def test_a_failure_does_not_stop_the_jobs_behind_it(self):
        _jobs.update({FIRST: reviewed(FIRST), SECOND: reviewed(SECOND)})
        self.failing = FIRST
        self.confirm([FIRST, SECOND])
        self.assertEqual(self.order, [FIRST, SECOND])
        self.assertEqual(_jobs[FIRST]["status"], "error")

    def test_single_confirmation_failure_is_reported_and_persisted(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        encoded = Path(directory.name) / "encoded.mp4"
        encoded.write_bytes(b"encoded")
        _jobs[FIRST] = {**reviewed(FIRST), "_out_path": str(encoded)}
        self.failing = FIRST

        response = self.confirm_one(FIRST)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_jobs[FIRST]["status"], "error")
        self.assertEqual(_jobs[FIRST]["log"], "Replace failed: upload died")
        jobs.persist_if_terminal.assert_called_with(FIRST)
        jobs.emit_job_update.assert_called_with(FIRST)
        jobs.append_csv_log.assert_called_once()
        self.assertEqual(jobs.append_csv_log.call_args.args[0][-1], "error")
        self.assertFalse(encoded.exists())

    def test_single_confirmation_cannot_run_twice(self):
        _jobs[FIRST] = reviewed(FIRST)
        self.assertEqual(self.confirm_one(FIRST).status_code, 200)
        self.assertEqual(self.confirm_one(FIRST).status_code, 409)
        self.assertEqual(self.order, [FIRST])

    def test_ids_must_be_a_list(self):
        self.assertEqual(self.confirm(FIRST).status_code, 400)
        self.assertEqual(self.client.post("/api/confirm-all").status_code, 400)


if __name__ == "__main__":
    unittest.main()
