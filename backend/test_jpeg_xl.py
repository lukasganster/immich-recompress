"""JPEG XL round trips, review/upload contracts, and failure handling."""

import shutil
import struct
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from backend.jobs import _encode_jxl_image, new_job, process_image_job
from backend.media import image_preview_path
from backend.routes import bp
from backend.state import _jobs, _queue_order


class PhotoOutputTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.asset_id = "12345678-1234-1234-1234-123456789abc"
        self.params = {"media": "image", "name": "holiday.JPEG", "photo_format": "jxl"}
        self.env = {"url": "https://immich.test", "api_key": "test-key"}
        self.stack.enter_context(patch("backend.jobs.WORK_DIR", str(self.directory)))
        self.stack.enter_context(patch("backend.jobs.BACKUP_DIR", str(self.directory / "backup")))
        self.stack.enter_context(patch("backend.jobs.shutil.which", return_value="/usr/bin/tool"))
        for name in ("emit_job_update", "broadcast_queue_update", "append_csv_log"):
            self.stack.enter_context(patch(f"backend.jobs.{name}"))
        self.replace = self.stack.enter_context(patch("backend.jobs.do_replace"))
        self.download = self.stack.enter_context(patch(
            "backend.jobs.download_original", side_effect=self.download_original))
        self.encoder = self.stack.enter_context(patch(
            "backend.jobs._encode_jxl_image", side_effect=self.encode))
        self.output = self.directory / f"{self.asset_id}_out.jxl"
        self.preview = Path(image_preview_path(str(self.output)))
        self.addCleanup(lambda: _jobs.pop(self.asset_id, None))
        app = Flask(__name__)
        app.register_blueprint(bp)
        self.client = app.test_client()

    def download_original(self, env, asset_id, path):
        Path(path).write_bytes(b"original jpeg" * 100)
        return True, ""

    def encode(self, src, out, asset_id, threads, preview=True):
        Path(out).write_bytes(b"encoded jxl" * 50)
        if preview:
            Path(image_preview_path(out)).write_bytes(b"png-preview")
        return True, ""

    def process(self, **overrides):
        params = {**self.params, **overrides}
        _jobs[self.asset_id] = new_job(self.asset_id, params)
        process_image_job(self.env, self.asset_id, params)
        return _jobs[self.asset_id]

    def test_review_serves_png_but_confirms_jxl_name_and_mime(self):
        job = self.process(photo_target_savings="not applicable")
        self.assertEqual(job["status"], "review")
        self.assertEqual(job["new_codec"], "JPEG XL")
        self.assertEqual(job["_upload_name"], "holiday.jxl")
        response = self.client.get(f"/api/preview/{self.asset_id}")
        self.assertEqual(response.mimetype, "image/png")
        self.assertEqual(response.data, b"png-preview")
        response.close()
        with patch("backend.routes.threading.Thread") as thread:
            response = self.client.post(f"/api/confirm/{self.asset_id}")
        self.assertEqual(response.status_code, 200)
        work = thread.call_args.kwargs["target"]
        self.assertEqual(work.args[2:4], ("holiday.jxl", str(self.output)))
        self.assertEqual(work.keywords, {"mime": "image/jxl"})

    def test_automatic_replacement_uploads_jxl_and_records_backup(self):
        job = self.process(confirm=False)
        self.assertEqual(self.replace.call_args.args[2:4], ("holiday.jxl", str(self.output)))
        self.assertEqual(self.replace.call_args.kwargs["mime"], "image/jxl")
        self.assertFalse(self.preview.exists())
        backup = self.directory / "backup" / f"{self.asset_id}_holiday.JPEG"
        self.assertEqual(backup.read_bytes(), b"original jpeg" * 100)
        self.assertEqual(job["_backup_path"], str(backup))

    def test_keep_original_retains_jxl_and_preview_until_discarded(self):
        job = self.process(replace=False, confirm=False)
        self.assertEqual(job["status"], "encoded")
        self.replace.assert_not_called()
        self.assertTrue(self.output.exists())
        self.assertTrue(self.preview.exists())
        with patch("backend.routes.persist_if_terminal"):
            response = self.client.post(f"/api/discard/{self.asset_id}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.preview.exists())

    def test_clearing_history_removes_preview_but_keeps_saved_output(self):
        self.process(replace=False)
        with patch("backend.routes.db_delete_jobs"):
            response = self.client.post("/api/clear")
        self.assertIn(self.asset_id, response.json["removed"])
        self.assertTrue(self.output.exists())
        self.assertFalse(self.preview.exists())

    def test_missing_either_tool_skips_before_download(self):
        for missing in ("cjxl", "djxl"):
            with self.subTest(missing=missing), patch(
                "backend.jobs.shutil.which", side_effect=lambda name: None if name == missing else "/tool"
            ):
                job = self.process()
                self.assertEqual(job["status"], "skipped")
                self.assertIn("libjxl-tools", job["log"])
        self.download.assert_not_called()

    def test_non_jpeg_inputs_are_not_converted_even_with_raw_enabled(self):
        for name in ("photo.dng", "photo.png", "photo.heic", "photo.jxl"):
            with self.subTest(name=name):
                self.assertEqual(self.process(name=name, compress_raw=True)["status"], "skipped")
        self.download.assert_not_called()
        self.encoder.assert_not_called()

    def test_encode_failure_removes_partial_output_and_keeps_original(self):
        def fail(*args, **kwargs):
            self.output.write_bytes(b"partial")
            self.preview.write_bytes(b"partial preview")
            return False, "invalid JPEG"
        self.encoder.side_effect = fail
        job = self.process()
        self.assertEqual(job["status"], "error")
        self.assertIn("invalid JPEG", job["log"])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.preview.exists())
        self.replace.assert_not_called()

    def test_cancellation_after_encode_prevents_replacement(self):
        def cancel(*args, **kwargs):
            result = self.encode(*args, **kwargs)
            _jobs[self.asset_id]["_cancel"] = True
            return result
        self.encoder.side_effect = cancel
        self.assertEqual(self.process(confirm=False)["status"], "cancelled")
        self.assertFalse(self.output.exists())
        self.replace.assert_not_called()

    def test_larger_outputs_are_skipped_even_with_zero_minimum(self):
        def larger(*args, **kwargs):
            self.output.write_bytes(b"x" * 2000)
            return True, ""
        self.encoder.side_effect = larger
        self.assertEqual(self.process(min_savings=0)["status"], "skipped")
        self.assertFalse(self.output.exists())
        self.replace.assert_not_called()

    def test_minimum_savings_applies_to_lossless_output(self):
        self.assertEqual(self.process(min_savings=90)["status"], "skipped")
        self.assertFalse(self.preview.exists())
        self.replace.assert_not_called()

    def test_jpeg_remains_the_default_and_uses_existing_encoder(self):
        def jpeg(src, out, *args):
            Path(out).write_bytes(b"jpeg" * 100)
            return True, "test q2", 50
        self.params.pop("photo_format")
        with patch("backend.jobs.compress_image_to_target", side_effect=jpeg):
            job = self.process()
        self.assertEqual(job["status"], "review")
        self.assertEqual(job["new_codec"], "JPEG")
        self.assertEqual(job["_output_mime"], "image/jpeg")
        self.assertEqual(job["_upload_name"], "holiday.JPEG")
        self.encoder.assert_not_called()

    def test_invalid_format_is_rejected_by_enqueue(self):
        for value in ("png", None, ["jxl"]):
            with self.subTest(value=value):
                response = self.client.post("/api/enqueue", json={"photo_format": value})
                self.assertEqual(response.status_code, 400)
        self.assertNotIn(self.asset_id, _queue_order)


@unittest.skipUnless(all(shutil.which(tool) for tool in ("cjxl", "djxl", "ffmpeg")),
                     "requires cjxl, djxl and ffmpeg")
class JpegXlToolTest(unittest.TestCase):
    def test_real_lossless_round_trip_including_metadata_and_oriented_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "original.jpg", Path(tmp) / "encoded.jxl"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc=size=64x32", "-frames:v", "1", str(src)],
                           check=True, capture_output=True)
            # A valid TIFF/Exif orientation tag and an XMP APP1 segment.
            exif = b"Exif\0\0II" + struct.pack("<HIHHHIHHI", 42, 8, 1, 274, 3, 1, 6, 0, 0)
            xmp = b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta xmlns:x='adobe:ns:meta/'/>"
            segments = b"".join(b"\xff\xe1" + struct.pack(">H", len(p) + 2) + p for p in (exif, xmp))
            original = src.read_bytes()
            src.write_bytes(original[:2] + segments + original[2:])
            ok, error = _encode_jxl_image(str(src), str(out), "tool-round-trip", threads=1)
            self.assertTrue(ok, error)
            restored = Path(tmp) / "restored.jpg"
            subprocess.run(["djxl", str(out), str(restored)], check=True, capture_output=True)
            self.assertEqual(src.read_bytes(), restored.read_bytes())
            png = Path(image_preview_path(str(out))).read_bytes()
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", png[16:24]), (32, 64))

    def test_corrupt_jpeg_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "invalid.jpg", Path(tmp) / "encoded.jxl"
            src.write_bytes(b"not an image")
            ok, _ = _encode_jxl_image(str(src), str(out), "corrupt-jpeg")
            self.assertFalse(ok)
            self.assertFalse(Path(image_preview_path(str(out))).exists())

    def test_mislabeled_png_is_not_lossily_transcoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "mislabeled.jpg", Path(tmp) / "encoded.jxl"
            src.write_bytes((Path(__file__).parent / "static" / "logo.png").read_bytes())
            ok, error = _encode_jxl_image(str(src), str(out), "mislabeled-jpeg")
            self.assertFalse(ok)
            self.assertIn("JPEG file contents", error)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
