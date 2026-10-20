"""Regression tests for hiding replacement Live Photo motion clips."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.jobs import do_recompress_motion, new_job
from backend.state import _jobs


class MotionReplacementTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.output = Path(directory) / "encoded.mp4"
        self.output.write_bytes(b"encoded motion clip")
        self.env = {"url": "https://immich.test", "api_key": "test-key"}
        self.hide_status = 200
        self.hide_error = None
        self.upload_status = "created"
        self.events = []
        self.visibility = "timeline"
        self.link = "old-clip"
        self.trashed = []
        self.stack.enter_context(
            patch.dict(_jobs, {"still": new_job("still", {"media": "motionphoto"})})
        )
        self.stack.enter_context(
            patch(
                "backend.immich_api.requests.get",
                return_value=Mock(status_code=200, json=lambda: {}),
            )
        )
        self.stack.enter_context(
            patch(
                "backend.immich_api.requests.post",
                side_effect=lambda *args, **kwargs: Mock(
                    status_code=201,
                    json=lambda: {"id": "new-clip", "status": self.upload_status},
                ),
            )
        )
        self.put = self.stack.enter_context(
            patch("backend.immich_api.requests.put", side_effect=self.update)
        )
        self.delete = self.stack.enter_context(
            patch("backend.immich_api.requests.delete", side_effect=self.trash)
        )
        self.csv_log = self.stack.enter_context(patch("backend.jobs.append_csv_log"))
        self.save = self.stack.enter_context(patch("backend.jobs.db_save_job"))
        self.stack.enter_context(patch("backend.jobs.broadcast"))

    def update(self, url, **kwargs):
        if url.endswith("/new-clip"):
            self.assertEqual(kwargs["json"], {"visibility": "hidden"})
            self.events.append("hide")
            if self.hide_error:
                raise self.hide_error
            if self.hide_status == 200:
                self.visibility = "hidden"
            return Mock(status_code=self.hide_status)
        self.assertTrue(url.endswith("/still"))
        self.events.append("link")
        self.link = kwargs["json"]["livePhotoVideoId"]
        return Mock(status_code=200)

    def trash(self, url, **kwargs):
        self.events.append("trash")
        self.trashed.extend(kwargs["json"]["ids"])
        return Mock(status_code=204)

    def replace(self):
        return do_recompress_motion(
            self.env, "still", "old-clip", str(self.output), "motion.mp4", 100, 50, 50
        )

    def assert_hide_failure_preserves_original(self):
        self.assertFalse(self.replace())
        self.assertEqual(self.events, ["hide"])
        self.assertEqual(self.visibility, "timeline")
        self.assertEqual(self.link, "old-clip")
        self.delete.assert_not_called()
        job = _jobs["still"]
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["new_id"], "new-clip")
        self.assertIn("new-clip", job["log"])
        self.assertIn("original kept", job["log"])
        self.assertIn("cleanup", job["log"])
        self.save.assert_called_once()
        self.assertEqual(self.save.call_args.args[0]["status"], "error")
        self.assertEqual(self.save.call_args.args[0]["new_id"], "new-clip")
        self.assertEqual(self.csv_log.call_args.args[0][-1], "error")
        self.assertFalse(self.output.exists())

    def test_rejected_hide_preserves_original_and_reports_uploaded_clip(self):
        self.hide_status = 403
        self.assert_hide_failure_preserves_original()

    def test_server_error_preserves_original_and_reports_uploaded_clip(self):
        self.hide_status = 500
        self.assert_hide_failure_preserves_original()

    def test_hide_timeout_preserves_original_and_reports_uploaded_clip(self):
        self.hide_error = requests.Timeout("simulated hide timeout")
        self.assert_hide_failure_preserves_original()

    def test_success_hides_before_linking_and_trashing(self):
        self.assertTrue(self.replace())
        self.assertEqual(self.events, ["hide", "link", "trash"])
        self.assertEqual(self.visibility, "hidden")
        self.assertEqual(self.link, "new-clip")
        self.assertEqual(self.trashed, ["old-clip"])
        self.assertEqual(_jobs["still"]["status"], "done")

    def test_duplicate_upload_does_not_modify_existing_clip(self):
        self.upload_status = "duplicate"
        self.assertFalse(self.replace())
        self.put.assert_not_called()
        self.delete.assert_not_called()
        self.assertIsNone(_jobs["still"]["new_id"])
        self.assertEqual(self.link, "old-clip")


if __name__ == "__main__":
    unittest.main()
