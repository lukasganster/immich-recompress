"""Regression tests for capture metadata in the replacement workflow."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from backend.immich_api import capture_metadata_fields
from backend.jobs import do_replace, new_job
from backend.state import _jobs


class ReplacementTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.output = Path(directory) / "encoded.mp4"
        self.output.write_bytes(b"encoded video")
        self.backup = Path(directory) / "original.mp4"
        self.backup.write_bytes(b"original video")
        self.stack.enter_context(patch.dict(_jobs, {
            "original": {**new_job("original", {}), "_backup_path": str(self.backup)},
        }))
        self.env = {"url": "https://immich.test", "api_key": "test-key"}
        self.source = {
            "fileCreatedAt": "2025-02-03T18:37:35.000Z",
            "fileModifiedAt": "2025-02-04T10:00:00.000Z",
            "exifInfo": {
                "dateTimeOriginal": "2025-02-03T18:37:35.000Z",
                "timeZone": "UTC",
                "latitude": 0,
                "longitude": 16.3738,
            },
        }
        self.target = {"dateTimeOriginal": "2026-10-10T14:31:29.000Z"}
        self.events = []
        self.update_status = 200
        self.trash_status = 204
        self.upload_status = "created"
        self.get = self.stack.enter_context(
            patch(
                "backend.immich_api.requests.get",
                side_effect=lambda *args, **kwargs: Mock(
                    status_code=200, json=lambda: self.source
                ),
            )
        )
        self.post = self.stack.enter_context(
            patch(
                "backend.immich_api.requests.post",
                side_effect=self.upload,
            )
        )
        self.put = self.stack.enter_context(
            patch(
                "backend.immich_api.requests.put",
                side_effect=self.update,
            )
        )
        self.delete = self.stack.enter_context(
            patch(
                "backend.immich_api.requests.delete",
                side_effect=self.trash,
            )
        )
        self.job_update = self.stack.enter_context(patch("backend.jobs.update_job"))
        for name in (
            "emit_job_update",
            "append_csv_log",
            "persist_if_terminal",
            "broadcast_queue_update",
        ):
            self.stack.enter_context(patch(f"backend.jobs.{name}"))

    def upload(self, *args, **kwargs):
        self.events.append("upload")
        return Mock(
            status_code=201,
            json=lambda: {"id": "replacement", "status": self.upload_status},
        )

    def update(self, url, **kwargs):
        if url.endswith("/copy"):
            self.events.append("copy")
            return Mock(status_code=204)
        self.events.append("restore")
        if self.update_status == 200:
            self.target.update(kwargs["json"])
        return Mock(status_code=self.update_status)

    def trash(self, *args, **kwargs):
        self.events.append("trash")
        return Mock(status_code=self.trash_status)

    def replace(self, mime="video/mp4"):
        return do_replace(
            self.env,
            "original",
            "encoded.mp4",
            str(self.output),
            100,
            50,
            50,
            "h264",
            mime,
        )

    def test_replacement_restores_capture_date_and_gps_before_trashing_original(self):
        self.assertTrue(self.replace())
        self.assertEqual(self.target["dateTimeOriginal"], "2025-02-03T18:37:35+00:00")
        self.assertEqual(self.target["latitude"], 0)
        self.assertEqual(self.target["longitude"], 16.3738)
        self.assertEqual(self.events, ["upload", "copy", "restore", "trash"])
        self.assertEqual(
            self.post.call_args.kwargs["data"]["fileCreatedAt"],
            self.source["fileCreatedAt"],
        )

    def test_replacement_removes_local_backup(self):
        self.assertTrue(self.replace())
        self.assertFalse(self.backup.exists())
        self.assertIsNone(_jobs["original"]["_backup_path"])

    def test_failed_trash_keeps_local_backup(self):
        self.trash_status = 500
        self.assertFalse(self.replace())
        self.assertTrue(self.backup.exists())

    def test_failed_upload_keeps_local_backup(self):
        self.upload_status = "duplicate"
        self.assertFalse(self.replace())
        self.assertTrue(self.backup.exists())

    def test_metadata_update_failure_keeps_original(self):
        self.update_status = 403
        self.assertFalse(self.replace())
        self.delete.assert_not_called()
        self.assertTrue(
            any(
                call.kwargs.get("status") == "error"
                for call in self.job_update.call_args_list
            )
        )

    def test_jxl_replacement_uploads_correct_format_and_cleans_preview(self):
        self.output = self.output.with_suffix(".jxl")
        self.output.write_bytes(b"encoded jxl")
        preview = Path(str(self.output) + ".preview.png")
        preview.write_bytes(b"preview")
        self.assertTrue(do_replace(
            self.env, "original", "photo.jxl", str(self.output),
            100, 50, 50, "JPEG", mime="image/jxl"))
        upload = self.post.call_args.kwargs["files"]["assetData"]
        self.assertEqual(upload[0], "photo.jxl")
        self.assertEqual(upload[2], "image/jxl")
        self.assertEqual(self.events, ["upload", "copy", "restore", "trash"])
        self.assertFalse(self.output.exists())
        self.assertFalse(preview.exists())

    def test_jxl_metadata_failure_keeps_original_and_cleans_output(self):
        self.update_status = 403
        self.output = self.output.with_suffix(".jxl")
        self.output.write_bytes(b"encoded jxl")
        preview = Path(str(self.output) + ".preview.png")
        preview.write_bytes(b"preview")
        self.assertFalse(do_replace(
            self.env, "original", "photo.jxl", str(self.output),
            100, 50, 50, "JPEG", mime="image/jxl"))
        self.delete.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(preview.exists())

    def test_missing_source_date_stops_before_upload(self):
        self.source = {"exifInfo": {}}
        self.assertFalse(self.replace())
        self.post.assert_not_called()
        self.delete.assert_not_called()

    def test_duplicate_upload_does_not_update_existing_asset(self):
        self.upload_status = "duplicate"
        self.assertFalse(self.replace())
        self.put.assert_not_called()
        self.delete.assert_not_called()

    def test_original_timezone_offset_and_empty_location(self):
        metadata = {
            "fileCreatedAt": "2020-07-16T12:00:00Z",
            "exifInfo": {
                "dateTimeOriginal": "2020-07-16T12:00:00Z",
                "timeZone": "UTC+2",
                "latitude": None,
                "longitude": None,
            },
        }
        self.assertEqual(
            capture_metadata_fields(metadata),
            {
                "dateTimeOriginal": "2020-07-16T14:00:00+02:00",
            },
        )

    def test_file_created_date_is_used_when_exif_date_is_missing(self):
        self.assertEqual(
            capture_metadata_fields(
                {
                    "fileCreatedAt": "2020-07-16T12:00:00Z",
                    "exifInfo": {},
                }
            ),
            {"dateTimeOriginal": "2020-07-16T12:00:00+00:00"},
        )


if __name__ == "__main__":
    unittest.main()
