"""Bulk replacement regressions: only listed reviews run, one at a time."""

import unittest
from unittest.mock import patch

from flask import Flask

from backend import routes
from backend.state import _jobs

FIRST = "30000000-0000-4000-8000-000000000001"
SECOND = "30000000-0000-4000-8000-000000000002"
THIRD = "30000000-0000-4000-8000-000000000003"
ENV = {"url": "https://immich.test", "api_keys": ["owner"], "api_key": "owner"}


class Inline:
    """Stands in for threading.Thread so a batch finishes inside the request."""

    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


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
        thread = patch.object(routes.threading, "Thread", Inline)
        thread.start()
        self.addCleanup(thread.stop)
        replace = patch.object(routes, "do_replace", side_effect=self.replace)
        replace.start()
        self.addCleanup(replace.stop)
        self.failing = None

    def replace(self, _env, asset_id, *_args, **_kwargs):
        self.order.append(asset_id)
        if asset_id == self.failing:
            raise RuntimeError("upload died")

    def confirm(self, ids):
        return self.client.post("/api/confirm-all", json={"ids": ids})

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

    def test_ids_must_be_a_list(self):
        self.assertEqual(self.confirm(FIRST).status_code, 400)
        self.assertEqual(self.client.post("/api/confirm-all").status_code, 400)


if __name__ == "__main__":
    unittest.main()
