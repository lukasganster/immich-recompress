"""Skipped jobs say why, and a video on the skip list is never downloaded."""

import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import Mock, patch

from backend import db, jobs, media
from backend.state import _jobs

ASSET = "40000000-0000-4000-8000-000000000001"
ENV = {"url": "https://immich.test", "api_key": "reader", "api_keys": ["reader"]}


class VideoSkipTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(_jobs, {ASSET: jobs.new_job(ASSET, {
            "name": "IMG_8138.MOV", "size": 3_500_000_000,
            "skip_codecs": "hevc,av1", "backup_dir": str(self.dir),
        })}))
        self.stack.enter_context(patch.object(jobs, "WORK_DIR", str(self.dir)))
        self.stack.enter_context(patch.object(jobs, "get_env", return_value=ENV))
        self.stack.enter_context(
            patch.object(jobs, "env_for_asset_verified", return_value=ENV))
        self.stack.enter_context(
            patch.object(jobs, "has_free_space", return_value=(True, 0)))
        self.stack.enter_context(patch.object(jobs, "append_csv_log"))
        self.stack.enter_context(patch.object(jobs, "broadcast"))
        self.probe = self.stack.enter_context(patch.object(jobs, "probe_original_codec"))
        self.download = self.stack.enter_context(
            patch.object(jobs, "download_original", side_effect=self.fake_download))
        self.local_probe = self.stack.enter_context(
            patch.object(jobs, "ffprobe_info", return_value=("hevc", 522.0)))

    def fake_download(self, env, asset_id, dest):
        Path(dest).write_bytes(b"original")
        return True, ""

    def test_listed_codec_is_skipped_without_downloading(self):
        self.probe.return_value = "hevc"
        jobs.process_job(ASSET)
        job = _jobs[ASSET]
        self.assertEqual(job["status"], "skipped")
        self.assertEqual(job["skip_reason"], "HEVC")
        self.assertIn("already HEVC", job["log"])
        self.assertEqual(job["codec"], "hevc")
        self.download.assert_not_called()

    def test_failed_probe_falls_back_to_checking_the_download(self):
        self.probe.return_value = None
        jobs.process_job(ASSET)
        job = _jobs[ASSET]
        self.download.assert_called_once()
        self.assertEqual(job["status"], "skipped")
        self.assertEqual(job["skip_reason"], "HEVC")

    def test_unlisted_codec_is_downloaded(self):
        self.probe.return_value = "h264"
        self.download.side_effect = None
        self.download.return_value = (False, "Immich HTTP 404")
        jobs.process_job(ASSET)
        self.download.assert_called_once()
        self.assertEqual(_jobs[ASSET]["status"], "error")
        self.assertIsNone(_jobs[ASSET]["skip_reason"])

    def test_cancel_during_the_probe_wins_over_the_skip(self):
        def cancel_then_find_hevc(env, asset_id):
            _jobs[ASSET]["_cancel"] = True
            return "hevc"
        self.probe.side_effect = cancel_then_find_hevc
        self.download.side_effect = None
        self.download.return_value = (False, "cancelled")
        jobs.process_job(ASSET)
        self.assertEqual(_jobs[ASSET]["status"], "cancelled")
        self.assertIsNone(_jobs[ASSET]["skip_reason"])

    def test_empty_skip_list_does_not_probe(self):
        _jobs[ASSET]["skip_codecs"] = ""
        self.download.side_effect = None
        self.download.return_value = (False, "Immich HTTP 404")
        jobs.process_job(ASSET)
        self.probe.assert_not_called()


class SavingsShortfallTest(unittest.TestCase):
    def test_output_that_grew(self):
        self.assertEqual(jobs.savings_shortfall(-5, -1.0, 10),
                         ("Output is not smaller than the original", "not smaller"))

    def test_output_below_the_minimum(self):
        log, reason = jobs.savings_shortfall(60, 6.04, 10)
        self.assertEqual(log, "Savings 6.0% < 10%")
        self.assertEqual(reason, "<10% saved")


class RemoteProbeTest(unittest.TestCase):
    def setUp(self):
        which = patch.object(media.shutil, "which", return_value="/usr/bin/ffprobe")
        which.start()
        self.addCleanup(which.stop)

    def test_reads_the_codec_with_the_key_as_a_header(self):
        output = json.dumps({"streams": [{"codec_type": "audio", "codec_name": "aac"},
                                         {"codec_type": "video", "codec_name": "hevc"}]})
        with patch.object(media.subprocess, "run",
                          return_value=Mock(stdout=output)) as run:
            codec = media.probe_remote_codec("https://immich.test/a", {"x-api-key": "k"})
        self.assertEqual(codec, "hevc")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-headers") + 1], "x-api-key: k\r\n")
        self.assertEqual(command[-1], "https://immich.test/a")

    def test_sends_the_key_only_to_a_verified_server_and_plain_containers(self):
        with patch.object(media.subprocess, "run", return_value=Mock(stdout="{}")) as run:
            media.probe_remote_codec("https://immich.test/a", {"x-api-key": "k"})
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-tls_verify") + 1], "1")
        allowed = command[command.index("-format_whitelist") + 1].split(",")
        self.assertIn("mov", allowed)
        self.assertNotIn("hls", allowed)
        self.assertNotIn("dash", allowed)

    def test_timeout_means_unknown(self):
        with patch.object(media.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("ffprobe", 60)):
            self.assertIsNone(media.probe_remote_codec("https://immich.test/a", {}))


class PersistedSkipReasonTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "jobs.db")
        db_path = patch.object(db, "DB_PATH", self.path)
        db_path.start()
        self.addCleanup(db_path.stop)

    def test_older_database_gains_the_column_and_restores_it(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, name TEXT, media TEXT,"
                "status TEXT, old_size INTEGER, new_size INTEGER, savings INTEGER,"
                "codec TEXT, new_codec TEXT, log TEXT, updated_at TEXT)")
        db.db_init()
        db.db_save_job({**jobs.new_job(ASSET, {"name": "IMG_8138.MOV"}),
                        "status": "skipped", "skip_reason": "HEVC"})
        with patch.dict(_jobs, clear=True), patch.object(jobs, "_queue_order", []):
            jobs.load_persisted_jobs()
            self.assertEqual(_jobs[ASSET]["skip_reason"], "HEVC")


if __name__ == "__main__":
    unittest.main()
