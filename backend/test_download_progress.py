"""Download byte reporting and progress through the real job/SSE path."""

import gzip
import json
import queue
import tempfile
import unittest
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from urllib3.response import HTTPResponse

from backend import immich_api, jobs
from backend.state import _jobs, _subscribers

ENV = {"url": "https://immich.test", "api_key": "test-key"}


class DownloadProgressTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.destination = self.directory / "original"
        self.now = 0.0
        self.reports = []
        self.cancelled = False
        self.stack.enter_context(
            patch.object(immich_api.time, "monotonic", side_effect=lambda: self.now)
        )
        self.sleep = self.stack.enter_context(patch.object(immich_api.time, "sleep"))
        self.stack.enter_context(
            patch.object(
                immich_api, "is_cancelled", side_effect=lambda _: self.cancelled
            )
        )
        self.get = self.stack.enter_context(patch.object(immich_api.requests, "get"))

    def response(self, chunks, headers=None, interval=1.1, status=200):
        def stream(chunk_size):
            for chunk in chunks:
                self.now += interval
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        response = MagicMock(status_code=status, headers=headers or {})
        response.__enter__.return_value = response
        response.iter_content.side_effect = stream
        return response

    def download(self, **kwargs):
        return immich_api.download_original(
            ENV,
            "asset",
            str(self.destination),
            progress_callback=lambda written, total: self.reports.append(
                (written, total)
            ),
            **kwargs,
        )

    def test_reports_written_bytes_and_prefers_content_length(self):
        self.get.return_value = self.response(
            [b"a", b"b", b"c", b"d"], {"Content-Length": "4"}
        )
        self.assertEqual(self.download(total_size=100), (True, ""))
        self.assertEqual(self.reports[0], (0, 4))
        self.assertIn((2, 4), self.reports)
        self.assertEqual(self.reports[-1], (4, 4))
        self.assertEqual(self.destination.read_bytes(), b"abcd")

    def test_invalid_or_missing_header_uses_known_size(self):
        for header in (None, "bad", "0", "-4", "1.5"):
            with self.subTest(header=header):
                self.reports.clear()
                headers = {} if header is None else {"Content-Length": header}
                self.get.return_value = self.response([b"abcd"], headers)
                self.assertEqual(self.download(total_size=8), (True, ""))
                self.assertEqual(self.reports[-1], (4, 8))

    def test_gzip_response_uses_original_size_after_real_decompression(self):
        original = b"original bytes" * 200_000
        compressed = gzip.compress(original)
        for known_size in (len(original), None):
            with self.subTest(known_size=known_size):
                self.reports.clear()
                headers = {
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(compressed)),
                }
                response = requests.Response()
                response.status_code = 200
                response.headers.update(headers)
                response.raw = HTTPResponse(
                    body=BytesIO(compressed),
                    headers=headers,
                    preload_content=False,
                    decode_content=False,
                )
                self.get.return_value = response
                self.assertEqual(self.download(total_size=known_size), (True, ""))
                self.assertEqual(self.destination.read_bytes(), original)
                self.assertEqual(self.reports[-1], (len(original), known_size))
                self.assertTrue(all(total == known_size for _, total in self.reports))

    def test_original_download_requests_identity_encoding(self):
        self.get.return_value = self.response([b"original"])
        self.assertEqual(self.download(), (True, ""))
        self.assertEqual(
            self.get.call_args.kwargs["headers"]["Accept-Encoding"], "identity"
        )
        self.assertEqual(
            self.get.call_args.kwargs["headers"]["x-api-key"], ENV["api_key"]
        )

    def test_identity_response_still_prefers_content_length(self):
        self.get.return_value = self.response(
            [b"original"], {"Content-Encoding": "IDENTITY", "Content-Length": "8"}
        )
        self.assertEqual(self.download(total_size=100), (True, ""))
        self.assertEqual(self.reports[-1], (8, 8))

    def test_unknown_total_still_reports_bytes(self):
        for size in (None, 0, -1, "bad"):
            with self.subTest(size=size):
                self.reports.clear()
                self.get.return_value = self.response([b"a", b"b"])
                self.assertEqual(self.download(total_size=size), (True, ""))
                self.assertIn((1, None), self.reports)
                self.assertEqual(self.reports[-1], (2, None))

    def test_throttles_intermediate_updates_but_always_reports_completion(self):
        self.get.return_value = self.response([b"a", b"b", b"c", b"d"], interval=0.4)
        self.assertEqual(self.download(total_size=4), (True, ""))
        self.assertEqual(self.reports, [(0, 4), (3, 4), (4, 4)])

    def test_fast_download_and_empty_chunks(self):
        self.get.return_value = self.response([b"", b"ab", b""], interval=0.1)
        self.assertEqual(self.download(total_size=2), (True, ""))
        self.assertEqual(self.reports, [(0, 2), (2, 2)])

    def test_underestimated_size_does_not_stop_byte_updates(self):
        self.get.return_value = self.response([b"a", b"b", b"c", b"d"])
        self.assertEqual(self.download(total_size=1), (True, ""))
        self.assertIn((2, 1), self.reports)
        self.assertIn((3, 1), self.reports)
        self.assertEqual(self.reports[-1], (4, 1))

    def test_retry_resets_progress_and_rewrites_destination(self):
        self.get.side_effect = [
            self.response([b"x", requests.ConnectionError("interrupted")]),
            self.response([b"a", b"b", b"c", b"d"]),
        ]
        self.assertEqual(self.download(total_size=4), (True, ""))
        self.assertEqual(self.reports[:3], [(0, 4), (1, 4), (0, 4)])
        self.assertEqual(self.reports[-1], (4, 4))
        self.assertEqual(self.destination.read_bytes(), b"abcd")
        self.sleep.assert_called_once_with(2)

    def test_cancelled_transfer_has_no_completion_report(self):
        def chunks(chunk_size):
            self.now += 1.1
            yield b"a"
            self.cancelled = True
            yield b"b"

        response = self.response([])
        response.iter_content.side_effect = chunks
        self.get.return_value = response
        self.assertEqual(self.download(total_size=2), (False, "cancelled"))
        self.assertEqual(self.reports, [(0, 2), (1, 2)])

    def test_cancel_at_end_of_stream_has_no_final_report(self):
        def chunks(chunk_size):
            yield b"a"
            self.cancelled = True

        response = self.response([])
        response.iter_content.side_effect = chunks
        self.get.return_value = response
        self.assertEqual(self.download(total_size=2), (False, "cancelled"))
        self.assertEqual(self.reports, [(0, 2)])

    def test_failed_transfer_has_no_completion_report(self):
        self.get.side_effect = [
            self.response([b"a", requests.ConnectionError("interrupted")])
            for _ in range(3)
        ]
        self.assertEqual(self.download(total_size=4), (False, "ConnectionError"))
        self.assertNotIn((4, 4), self.reports)
        self.assertEqual(self.get.call_count, 3)

    def test_disk_failure_is_not_retried(self):
        self.get.return_value = self.response([b"a"])
        with patch("builtins.open", side_effect=OSError("disk full")):
            self.assertEqual(self.download(), (False, "Write error: disk full"))
        self.get.assert_called_once()
        self.sleep.assert_not_called()

    def test_permanent_http_error_has_no_progress(self):
        self.get.return_value = self.response([], status=404)
        self.assertEqual(self.download(), (False, "Immich HTTP 404"))
        self.assertEqual(self.reports, [])
        self.get.assert_called_once()

    def test_existing_callers_need_no_callback(self):
        self.get.return_value = self.response([b"original"])
        self.assertEqual(
            immich_api.download_original(ENV, "asset", str(self.destination)),
            (True, ""),
        )
        self.assertEqual(self.destination.read_bytes(), b"original")


class JobDownloadProgressTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(_jobs, clear=True))
        self.stack.enter_context(patch.object(jobs, "WORK_DIR", str(self.directory)))
        self.stack.enter_context(
            patch.object(jobs, "BACKUP_DIR", str(self.directory / "backup"))
        )
        for name, value in (
            ("get_env", ENV),
            ("env_for_asset_verified", ENV),
            ("has_free_space", (True, 0)),
            ("ffprobe_info", ("h264", 1)),
        ):
            self.stack.enter_context(patch.object(jobs, name, return_value=value))
        self.stack.enter_context(patch.object(jobs, "append_csv_log"))
        self.stack.enter_context(
            patch.object(
                jobs,
                "fetch_asset",
                side_effect=lambda env, asset_id: (
                    {"livePhotoVideoId": "motion"}
                    if asset_id == "still"
                    else {
                        "originalFileName": "motion.mov",
                        "fileSizeInByte": 4 * 1024 * 1024,
                    }
                ),
            )
        )
        self.stack.enter_context(
            patch.object(jobs, "run_handbrake", side_effect=self.encode_video)
        )
        self.stack.enter_context(
            patch.object(
                jobs, "compress_image_to_target", side_effect=self.encode_image
            )
        )
        self.now = 0.0
        self.stack.enter_context(
            patch.object(immich_api.time, "monotonic", side_effect=lambda: self.now)
        )
        self.get = self.stack.enter_context(patch.object(immich_api.requests, "get"))
        self.events = queue.Queue()
        _subscribers.append(self.events)
        self.stack.callback(_subscribers.remove, self.events)
        self.observations = []

    def encode_video(self, cmd, asset_id, duration):
        (self.directory / f"{asset_id}_out.mp4").write_bytes(b"encoded")
        return True

    def encode_image(self, src, out, old_size, target_pct, asset_id):
        Path(out).write_bytes(b"encoded")
        return True, 80, 99

    def run_job(
        self, media, download_only=False, motion_action="recompress", known_total=True
    ):
        def stream(chunk_size):
            for index in range(4):
                self.now += 1.1
                yield b"x" * (1024 * 1024)
                self.observations.append(dict(_jobs["still"]))

        response = MagicMock(status_code=200, headers={})
        response.__enter__.return_value = response
        response.iter_content.side_effect = stream
        self.get.return_value = response
        params = {
            "media": media,
            "name": "photo.jpg" if media == "image" else "video.mp4",
            "size": 4 * 1024 * 1024 if known_total else None,
            "download_only": download_only,
            "skip_codecs": "",
            "motion_action": motion_action,
            "replace": motion_action == "remove",
            "confirm": True,
        }
        # The queued Live Photo size belongs to the still, not its motion clip.
        if media == "motionphoto":
            params["size"] = 100
        _jobs["still"] = jobs.new_job("still", params)
        jobs.process_job("still")
        return [
            json.loads(data)
            for event, data in list(self.events.queue)
            if event == "job_update"
        ]

    def assert_live_progress(self, updates):
        live = [
            u
            for u in updates
            if u["status"] == "downloading" and 0 < u["progress"] < 100
        ]
        self.assertEqual([u["progress"] for u in live], [25.0, 50.0, 75.0])
        self.assertTrue(all(u["id"] == "still" for u in live))
        self.assertTrue(all("of 4.0 MB" in u["log"] for u in live))
        self.assertEqual(
            [o["progress"] for o in self.observations[:3]], [25.0, 50.0, 75.0]
        )
        self.assertNotIn("motion", _jobs)

    def test_download_only_reports_live_progress_for_every_media_type(self):
        for media in ("video", "image", "motionphoto"):
            with self.subTest(media=media):
                self.events.queue.clear()
                self.observations.clear()
                updates = self.run_job(media, download_only=True)
                self.assert_live_progress(updates)
                self.assertEqual(_jobs["still"]["status"], "downloaded")

    def test_encoding_resets_download_progress_for_every_media_type(self):
        for media in ("video", "image", "motionphoto"):
            with self.subTest(media=media):
                self.events.queue.clear()
                self.observations.clear()
                updates = self.run_job(media)
                self.assert_live_progress(updates)
                encoding = [u for u in updates if u["status"] == "encoding"]
                self.assertEqual(encoding[0]["progress"], 0.0)
                self.assertEqual(_jobs["still"]["status"], "encoded")

    def test_motion_backup_reports_progress_on_the_still_job(self):
        updates = self.run_job("motionphoto", motion_action="remove")
        self.assert_live_progress(updates)
        self.assertTrue(any("backup" in u["log"].lower() for u in updates))
        self.assertEqual(_jobs["still"]["status"], "review")

    def test_unknown_total_updates_byte_count_without_guessing_percentage(self):
        updates = self.run_job("video", download_only=True, known_total=False)
        downloading = [u for u in updates if u["status"] == "downloading"]
        self.assertTrue(any("2.0 MB downloaded" in u["log"] for u in downloading))
        self.assertTrue(all(u["progress"] == 0.0 for u in downloading))


if __name__ == "__main__":
    unittest.main()
