"""A Photos tab job on a Live Photo still with motion setting Recompress also
re-encodes the linked video. Each part is checked against Min. Savings on its own."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backend import jobs
from backend.jobs import do_replace, new_job, process_image_job
from backend.state import _jobs

STILL = "70000000-0000-4000-8000-000000000001"
VIDEO = "70000000-0000-4000-8000-000000000002"
ENV = {"url": "https://immich.test", "api_key": "k"}


class CombinedJobTest(unittest.TestCase):
    """Encoders are mocked: each writes an output of the size the test picks."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.work = self.dir / "work"
        self.stack.enter_context(patch.object(jobs, "WORK_DIR", str(self.work)))
        for name in ("emit_job_update", "broadcast_queue_update", "append_csv_log"):
            self.stack.enter_context(patch.object(jobs, name))
        self.replace = self.stack.enter_context(patch.object(jobs, "do_replace"))
        self.stack.enter_context(patch.object(jobs, "download_original", side_effect=self.download))
        self.stack.enter_context(patch.object(jobs, "_encode_jxl_image", side_effect=self.encode_still))
        self.handbrake = self.stack.enter_context(
            patch.object(jobs, "run_handbrake", side_effect=self.encode_video))
        self.stack.enter_context(patch.object(jobs, "ffprobe_info", return_value=("h264", 2.0)))
        self.stack.enter_context(patch.object(jobs.shutil, "which", return_value="/usr/bin/tool"))
        self.still_out, self.video_out = 500, 4000
        self.addCleanup(lambda: _jobs.pop(STILL, None))

    def download(self, env, asset_id, dest, **kwargs):
        if asset_id == VIDEO:
            Path(dest).write_bytes(b"v" * 10_000)
        else:
            Path(dest).write_bytes(b"\xff\xd8" + b"s" * 998)
        return True, ""

    def encode_still(self, src, out, asset_id, threads, preview=True, distance=0, pixels=False):
        Path(out).write_bytes(b"j" * self.still_out)
        return True, ""

    def encode_video(self, cmd, asset_id, duration):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"h" * self.video_out)
        return True

    def run_job(self, **overrides):
        params = {"media": "image", "name": "PXL.jpg", "photo_format": "jxl", "min_savings": 10,
                  "motion_action": "recompress", "backup_dir": str(self.dir / "backup"),
                  "_source_detail": {"livePhotoVideoId": VIDEO},
                  "_motion_detail": {"id": VIDEO, "originalFileName": "PXL.mp4",
                                     "fileSizeInByte": 10_000}, **overrides}
        _jobs[STILL] = new_job(STILL, params)
        process_image_job(ENV, STILL, params)
        return _jobs[STILL]

    def leftovers(self):
        return sorted(p.name for p in self.work.iterdir()) if self.work.exists() else []

    def test_both_parts_pass(self):
        job = self.run_job()
        self.assertEqual(job["status"], "review", job["log"])
        self.assertFalse(job["still_kept"])
        self.assertEqual((job["video_old_size"], job["video_new_size"]), (10_000, 4000))
        self.assertEqual(job["savings"], (1000 - 500) + (10_000 - 4000))
        self.assertTrue(Path(job["_motion_out_path"]).is_file())

    def test_growing_video_is_dropped_and_never_uploaded(self):
        self.video_out = 12_000
        job = self.run_job(confirm=False)
        self.assertEqual(job["status"], "encoded", job["log"])
        self.assertIsNone(job["_motion_out_path"])
        self.assertIsNone(job["video_new_size"])
        self.assertIn("kept, not smaller", job["video_note"])
        self.assertEqual(job["savings"], 500)
        self.replace.assert_called_once()
        self.assertFalse([n for n in self.leftovers() if "motion" in n])

    def test_still_fails_video_passes(self):
        self.still_out = 980
        job = self.run_job()
        self.assertEqual(job["status"], "review", job["log"])
        self.assertTrue(job["still_kept"])
        self.assertIsNone(job["_out_path"])
        self.assertEqual(job["savings"], 6000)
        self.assertTrue(Path(job["_motion_out_path"]).is_file())

    def test_both_fail_are_skipped_with_both_reasons(self):
        self.still_out, self.video_out = 980, 12_000
        job = self.run_job()
        self.assertEqual(job["status"], "skipped")
        self.assertIn("Photo:", job["log"])
        self.assertIn("Motion video kept, not smaller", job["log"])
        self.assertEqual(self.leftovers(), [])
        self.replace.assert_not_called()

    def test_cancel_during_the_video_encode_replaces_nothing(self):
        def cancel(cmd, asset_id, duration):
            _jobs[STILL]["_cancel"] = True
            return False
        self.handbrake.side_effect = cancel
        job = self.run_job(confirm=False)
        self.assertEqual(job["status"], "cancelled")
        self.replace.assert_not_called()
        self.assertEqual(self.leftovers(), [])

    def test_keep_does_not_touch_the_video(self):
        job = self.run_job(motion_action="keep")
        self.assertEqual(job["status"], "review")
        self.handbrake.assert_not_called()
        self.assertIsNone(job["video_new_size"])


class CombinedReplaceTest(unittest.TestCase):
    """Order of the Immich calls when the confirmed review holds a video part."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.still, self.clip = directory / "out.jxl", directory / "motion.mp4"
        self.calls, self.link_ok = [], True
        self.detail = {"livePhotoVideoId": VIDEO}

        def record(name, result):
            def call(env, *args, **kwargs):
                self.calls.append((name, *args[:2]))
                if name == "upload_new_asset":
                    return ("new-clip" if args[0].endswith(".mp4") else "new-still"), "created"
                return result() if callable(result) else result
            return call
        for name, result in (
            ("fetch_asset", lambda: self.detail),
            ("get_asset_meta", {"exifInfo": {}}),
            ("upload_new_asset", None),
            ("copy_asset_metadata", True),
            ("update_asset", True),
            ("set_live_photo_link", lambda: self.link_ok),
            ("trash_asset", True),
        ):
            self.stack.enter_context(patch.object(jobs, name, side_effect=record(name, result)))
        self.stack.enter_context(patch.object(jobs, "capture_metadata_fields",
                                              return_value={"dateTimeOriginal": "x"}))
        for name in ("emit_job_update", "append_csv_log", "persist_if_terminal",
                     "broadcast_queue_update"):
            self.stack.enter_context(patch.object(jobs, name))
        self.addCleanup(lambda: _jobs.pop(STILL, None))

    def replace(self, still_kept=False):
        self.still.write_bytes(b"jxl")
        self.clip.write_bytes(b"mp4")
        _jobs[STILL] = {**new_job(STILL, {"media": "image", "motion_action": "recompress"}),
                        "_motion_out_path": str(self.clip), "_video_id": VIDEO,
                        "_video_name": "PXL.mp4", "video_old_size": 10_000,
                        "video_new_size": 4000, "still_kept": still_kept}
        return do_replace(ENV, STILL, "PXL.jxl", None if still_kept else str(self.still),
                          1000, 500, 500, "JPG", mime="image/jxl")

    def names(self):
        return [c[0] for c in self.calls]

    def test_both_parts_upload_and_link_before_any_trash(self):
        self.assertTrue(self.replace())
        order = self.names()
        self.assertEqual([c[1] for c in self.calls if c[0] == "upload_new_asset"],
                         [str(self.still), str(self.clip)])
        self.assertLess(order.index("set_live_photo_link"), order.index("trash_asset"))
        self.assertIn(("set_live_photo_link", "new-still", "new-clip"),
                      [(c[0], c[1], c[2]) for c in self.calls if len(c) > 2])
        trashed = [c[1] for c in self.calls if c[0] == "trash_asset"]
        self.assertEqual(trashed, [STILL, VIDEO])
        self.assertEqual(_jobs[STILL]["status"], "done")

    def test_failed_link_trashes_only_the_new_uploads(self):
        self.link_ok = False
        self.assertFalse(self.replace())
        trashed = [c[1] for c in self.calls if c[0] == "trash_asset"]
        self.assertEqual(sorted(trashed), ["new-clip", "new-still"])
        self.assertEqual(_jobs[STILL]["status"], "error")

    def test_kept_still_repoints_it_to_the_new_clip(self):
        self.assertTrue(self.replace(still_kept=True))
        uploads = [c[1] for c in self.calls if c[0] == "upload_new_asset"]
        self.assertEqual(uploads, [str(self.clip)])
        self.assertIn(("set_live_photo_link", STILL, "new-clip"),
                      [(c[0], c[1], c[2]) for c in self.calls if len(c) > 2])
        trashed = [c[1] for c in self.calls if c[0] == "trash_asset"]
        self.assertEqual(trashed, [VIDEO])

    def test_changed_link_stops_before_upload(self):
        self.detail = {"livePhotoVideoId": "another"}
        self.assertFalse(self.replace())
        self.assertNotIn("upload_new_asset", self.names())


if __name__ == "__main__":
    unittest.main()
