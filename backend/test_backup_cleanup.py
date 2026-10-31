"""Regression tests for removing local backups across the job pipelines."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backend.jobs import new_job, process_job, record_backup
from backend.state import _jobs

ENV = {"url": "https://immich.test", "api_key": "test-key"}
ORIGINAL = b"o" * 1000


class BackupCleanupTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.work = self.directory / "work"
        self.backups = self.directory / "backup"
        self.encoded = b"e" * 10
        self.encode_ok = True
        self.stack.enter_context(patch.dict(_jobs))
        self.stack.enter_context(patch("backend.jobs.WORK_DIR", str(self.work)))
        self.stack.enter_context(patch("backend.jobs.BACKUP_DIR", str(self.backups)))
        for name in ("emit_job_update", "broadcast_queue_update", "append_csv_log",
                     "persist_if_terminal", "build_handbrake_cmd"):
            self.stack.enter_context(patch(f"backend.jobs.{name}"))
        self.fake("get_env", return_value=ENV)
        self.fake("env_for_asset_verified", return_value=ENV)
        self.fake("ffprobe_info", return_value=("h264", 1.0))
        self.fake("download_original", side_effect=self.download)
        self.fake("run_handbrake", side_effect=self.encode)
        self.fake("fetch_asset", side_effect=self.asset)
        self.fake("get_asset_meta", return_value={})
        self.fake("capture_metadata_fields", return_value={"dateTimeOriginal": "x"})
        self.fake("upload_new_asset", return_value=("new", "created"))
        self.fake("copy_asset_metadata", return_value=True)
        self.fake("update_asset", return_value=True)
        self.fake("set_live_photo_link", return_value=True)
        self.trash = self.fake("trash_asset", return_value=True)

    def fake(self, name, **kwargs):
        return self.stack.enter_context(patch(f"backend.jobs.{name}", **kwargs))

    def download(self, env, asset_id, path):
        Path(path).write_bytes(ORIGINAL)
        return True, ""

    def encode(self, cmd, asset_id, duration):
        (self.work / f"{asset_id}_out.mp4").write_bytes(self.encoded)
        return self.encode_ok

    def asset(self, env, asset_id):
        if asset_id == "still":
            return {"livePhotoVideoId": "clip"}
        return {"originalFileName": "clip.mov", "fileSizeInByte": len(ORIGINAL)}

    def run_job(self, asset_id, **params):
        params = {"name": "clip.mov", "confirm": False, **params}
        _jobs[asset_id] = new_job(asset_id, params)
        process_job(asset_id)
        return _jobs[asset_id]

    def motion(self, **params):
        return self.run_job("still", media="motionphoto", motion_action="recompress", **params)

    def assert_no_backups(self):
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_automatic_video_replacement_removes_backup(self):
        self.assertEqual(self.run_job("video", media="video")["status"], "done")
        self.assert_no_backups()

    def test_failed_video_replacement_keeps_backup(self):
        self.trash.return_value = False
        self.assertEqual(self.run_job("video", media="video")["status"], "error")
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_automatic_motion_recompress_removes_backup(self):
        self.assertEqual(self.motion()["status"], "done")
        self.assert_no_backups()

    def test_automatic_motion_strip_removes_backup(self):
        job = self.run_job("still", media="motionphoto", motion_action="remove")
        self.assertEqual(job["status"], "done")
        self.assert_no_backups()

    def test_failed_motion_replacement_keeps_backup(self):
        self.trash.return_value = False
        self.assertEqual(self.motion()["status"], "error")
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_motion_encode_failure_drops_backup(self):
        self.encode_ok = False
        self.assertEqual(self.motion()["status"], "error")
        self.assert_no_backups()

    def test_motion_low_savings_drops_backup(self):
        self.encoded = b"e" * 995
        self.assertEqual(self.motion()["status"], "skipped")
        self.assert_no_backups()

    def test_motion_kept_original_drops_backup(self):
        self.assertEqual(self.motion(replace=False)["status"], "encoded")
        self.assert_no_backups()

    def test_backup_of_cleared_job_is_dropped(self):
        backup = self.directory / "orphan.mov"
        backup.write_bytes(ORIGINAL)
        record_backup("cleared", str(backup))
        self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
