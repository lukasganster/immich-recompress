"""Server-wide concurrency settings and all-media scheduler integration."""

import sqlite3
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from backend import db, jobs, routes, scheduler, immich_api, media
from backend.state import (
    _jobs,
    _queue_order,
    _scheduler,
)

VIDEO = "50000000-0000-4000-8000-000000000001"
PHOTO = "50000000-0000-4000-8000-000000000002"
MOTION = "50000000-0000-4000-8000-000000000003"
CLIP = "50000000-0000-4000-8000-000000000004"
ENV = {"url": "https://immich.test", "api_key": "key", "api_keys": ["key"]}


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(
            patch.object(db, "DB_PATH", str(self.directory / "history.sqlite"))
        )
        db.db_init()
        self.stack.enter_context(patch.dict(_jobs, clear=True))
        self.order = list(_queue_order)
        _queue_order.clear()
        self.stack.callback(lambda: _queue_order.__setitem__(slice(None), self.order))
        with _scheduler.condition:
            _scheduler.pending.clear()
            _scheduler.active.clear()
            _scheduler.assets.clear()
            _scheduler.concurrent_jobs = 1
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        self.stop = threading.Event()
        self.thread = None
        self.stack.callback(self.shutdown)

    def start_scheduler(self):
        self.thread = threading.Thread(
            target=scheduler.run_scheduler, args=(self.stop,)
        )
        self.thread.start()

    def wait_idle(self):
        with _scheduler.condition:
            self.assertTrue(
                _scheduler.condition.wait_for(
                    lambda: not _scheduler.active and not _scheduler.pending, timeout=3
                )
            )

    def shutdown(self):
        self.stop.set()
        scheduler.wake()
        if self.thread:
            self.thread.join(timeout=2)
            self.assertFalse(self.thread.is_alive())
        with _scheduler.condition:
            _scheduler.pending.clear()
            _scheduler.assets.clear()

    def test_settings_are_global_validated_and_persist_after_reload(self):
        self.assertEqual(self.client.get("/api/settings").json, {"concurrent_jobs": 1})
        for value in [0, 9, -1, True, 2.0, "3", [], {}, None]:
            with self.subTest(value=value):
                response = self.client.put(
                    "/api/settings", json={"concurrent_jobs": value}
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json=[]).status_code, 400)
        self.assertEqual(
            self.client.put("/api/settings", json={"concurrent_jobs": 3}).json,
            {"concurrent_jobs": 3},
        )
        another_session = self.client.application.test_client()
        self.assertEqual(
            another_session.get("/api/settings").json["concurrent_jobs"], 3
        )
        self.assertEqual(another_session.get("/api/jobs").json["concurrent_jobs"], 3)
        scheduler.set_concurrency(1)
        scheduler.set_concurrency(db.db_load_concurrency())
        self.assertEqual(scheduler.concurrency(), 3)

    def test_failed_setting_write_keeps_the_current_limit(self):
        with patch.object(
            scheduler,
            "db_save_concurrency",
            side_effect=sqlite3.OperationalError("read only"),
        ):
            response = self.client.put("/api/settings", json={"concurrent_jobs": 4})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(scheduler.concurrency(), 1)
        self.assertEqual(db.db_load_concurrency(), 1)

    def test_settings_broadcast_so_other_sessions_refresh(self):
        with patch.object(routes, "broadcast_queue_update") as broadcast:
            self.client.put("/api/settings", json={"concurrent_jobs": 2})
        broadcast.assert_called_once()

    def test_uploads_in_the_same_millisecond_have_distinct_device_ids(self):
        output = self.directory / "output.mp4"
        output.write_bytes(b"encoded")
        response = MagicMock(status_code=201)
        response.json.return_value = {"id": "uploaded", "status": "created"}
        with (
            patch.object(immich_api.time, "time", return_value=1800000000.123),
            patch.object(immich_api.requests, "post", return_value=response) as post,
        ):
            for _ in range(2):
                immich_api.upload_new_asset(
                    ENV, str(output), "video.mp4", None, None, "video/mp4"
                )
        identities = [
            call.kwargs["data"]["deviceAssetId"] for call in post.call_args_list
        ]
        self.assertEqual(len(set(identities)), 2)

    def test_concurrent_csv_writes_keep_one_header_and_all_rows(self):
        import csv

        log = self.directory / "log.csv"
        barrier = threading.Barrier(8)

        def write(index):
            barrier.wait(timeout=2)
            media.append_csv_log(
                [str(index), str(index), "video.mp4", "h264", 4, 2, 2, "done"]
            )

        with (
            patch.object(media, "CSV_LOG", str(log)),
            patch.object(media, "WORK_DIR", str(self.directory)),
        ):
            threads = [
                threading.Thread(target=write, args=(index,)) for index in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
        with log.open() as source:
            rows = list(csv.reader(source))
        self.assertEqual(rows[0][0], "timestamp")
        self.assertEqual(len(rows), 9)
        self.assertEqual(
            {row[1] for row in rows[1:]}, {str(index) for index in range(8)}
        )

    def test_cancelling_a_queued_job_removes_it_without_starting_work(self):
        response = self.client.post("/api/enqueue", json={"ids": [VIDEO]})
        self.assertEqual(response.json["added"], [VIDEO])
        self.assertTrue(scheduler.has_task(VIDEO))
        response = self.client.post(f"/api/cancel/{VIDEO}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(scheduler.has_task(VIDEO))
        self.assertEqual(_jobs[VIDEO]["status"], "cancelled")
        self.assertEqual(db.db_load_jobs()[0]["status"], "cancelled")

    def test_job_cancelled_while_being_picked_up_stays_cancelled(self):
        _jobs[VIDEO] = jobs.new_job(VIDEO, {"name": "clip.mov"})
        _queue_order.append(VIDEO)
        self.assertEqual(self.client.post(f"/api/cancel/{VIDEO}").status_code, 200)
        with patch.object(jobs, "emit_job_update"), patch.object(
            jobs, "broadcast_queue_update"
        ):
            jobs._task_notify(VIDEO, scheduler.TaskEvent.PREPARING)
            jobs._task_notify(VIDEO, scheduler.WaitReason.DISK)
        self.assertEqual(_jobs[VIDEO]["status"], "cancelled")

    def test_related_asset_wait_explains_which_job_holds_the_asset(self):
        _jobs[MOTION] = jobs.new_job(MOTION, {"name": "live.heic"})
        _jobs[MOTION]["status"] = "review"
        _jobs[CLIP] = jobs.new_job(CLIP, {"name": "clip.mov"})
        with _scheduler.condition:
            _scheduler.waiting_on[CLIP] = MOTION
        self.addCleanup(_scheduler.waiting_on.clear)
        with patch.object(jobs, "emit_job_update"), patch.object(
            jobs, "broadcast_queue_update"
        ):
            jobs._task_notify(CLIP, scheduler.WaitReason.ASSET)
        self.assertEqual(
            _jobs[CLIP]["log"],
            "Waiting for related asset: live.heic is awaiting review",
        )

    def test_skipped_video_and_unsupported_photo_do_not_wait_for_disk(self):
        self.stack.enter_context(patch.object(jobs, "get_env", return_value=ENV))
        self.stack.enter_context(
            patch.object(jobs, "env_for_asset_verified", return_value=ENV)
        )
        self.stack.enter_context(
            patch.object(jobs, "fetch_asset", return_value={"fileSizeInByte": 10**12})
        )
        self.stack.enter_context(
            patch.object(jobs, "probe_original_codec", return_value="hevc")
        )
        self.stack.enter_context(patch.object(jobs, "append_csv_log"))
        usage = self.stack.enter_context(patch.object(scheduler.shutil, "disk_usage"))
        usage.return_value.free = 0
        self.start_scheduler()
        response = self.client.post(
            "/api/enqueue",
            json={
                "ids": [VIDEO, PHOTO],
                "names": {VIDEO: "video.mov", PHOTO: "photo.png"},
                "medias": {VIDEO: "video", PHOTO: "image"},
            },
        )
        self.assertEqual(response.json["added"], [VIDEO, PHOTO])
        self.wait_idle()
        self.assertEqual(_jobs[VIDEO]["status"], "skipped")
        self.assertEqual(_jobs[PHOTO]["status"], "skipped")

    def test_trashed_originals_or_motion_clips_are_not_processed(self):
        details = {
            VIDEO: {"isTrashed": True},
            MOTION: {"livePhotoVideoId": CLIP},
            CLIP: {"isTrashed": True},
        }
        self.stack.enter_context(patch.object(jobs, "get_env", return_value=ENV))
        self.stack.enter_context(
            patch.object(jobs, "env_for_asset_verified", return_value=ENV)
        )
        self.stack.enter_context(
            patch.object(jobs, "fetch_asset", side_effect=lambda env, aid: details[aid])
        )
        download = self.stack.enter_context(patch.object(jobs, "download_original"))
        self.start_scheduler()
        self.client.post(
            "/api/enqueue",
            json={
                "ids": [VIDEO, MOTION],
                "medias": {VIDEO: "video", MOTION: "motionphoto"},
            },
        )
        self.wait_idle()
        for aid in [VIDEO, MOTION]:
            self.assertEqual(_jobs[aid]["status"], "error")
            self.assertIn("Immich trash", _jobs[aid]["log"])
        download.assert_not_called()

    def test_mixed_media_overlap_download_encode_and_automatic_upload(self):
        scheduler.set_concurrency(3)
        work = self.directory / "work"
        backup = self.directory / "backup"
        self.stack.enter_context(patch.object(jobs, "WORK_DIR", str(work)))
        self.stack.enter_context(patch.object(jobs, "BACKUP_DIR", str(backup)))
        self.stack.enter_context(patch.object(jobs, "get_env", return_value=ENV))
        self.stack.enter_context(
            patch.object(jobs, "env_for_asset_verified", return_value=ENV)
        )
        self.stack.enter_context(
            patch.object(jobs, "ffprobe_info", return_value=("h264", 1))
        )
        self.stack.enter_context(patch.object(jobs, "append_csv_log"))
        details = {
            VIDEO: {"originalFileName": "video.mp4", "fileSizeInByte": 4},
            PHOTO: {"originalFileName": "photo.jpg", "fileSizeInByte": 4},
            MOTION: {
                "originalFileName": "live.jpg",
                "livePhotoVideoId": CLIP,
                "fileSizeInByte": 100,
            },
            CLIP: {"originalFileName": "clip.mov", "fileSizeInByte": 4},
        }
        self.stack.enter_context(
            patch.object(jobs, "fetch_asset", side_effect=lambda env, aid: details[aid])
        )
        download_barrier, encode_barrier, upload_barrier = (
            threading.Barrier(3) for _ in range(3)
        )
        stages = []

        def get(url, **kwargs):
            aid = url.split("/")[-2]
            response = MagicMock(status_code=200, headers={"Content-Length": "4"})
            response.__enter__.return_value = response

            def stream(chunk_size):
                stages.append(("download", MOTION if aid == CLIP else aid))
                download_barrier.wait(timeout=2)
                yield b"abcd"

            response.iter_content.side_effect = stream
            return response

        def encode_video(cmd, aid, duration):
            stages.append(("encode", aid))
            encode_barrier.wait(timeout=2)
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"ab")
            return True

        def encode_photo(src, out, *args):
            stages.append(("encode", PHOTO))
            encode_barrier.wait(timeout=2)
            Path(out).write_bytes(b"ab")
            return True, 80, 50

        def upload(env, aid, *args, **kwargs):
            stages.append(("upload", aid))
            upload_barrier.wait(timeout=2)
            jobs.update_job(aid, status="done")

        self.stack.enter_context(
            patch("backend.immich_api.requests.get", side_effect=get)
        )
        for name in ["post", "put", "delete"]:
            self.stack.enter_context(
                patch(
                    f"backend.immich_api.requests.{name}",
                    side_effect=AssertionError("unexpected network mutation"),
                )
            )
        self.stack.enter_context(
            patch.object(jobs, "run_handbrake", side_effect=encode_video)
        )
        self.stack.enter_context(
            patch.object(jobs, "compress_image_to_target", side_effect=encode_photo)
        )
        self.stack.enter_context(patch.object(jobs, "do_replace", side_effect=upload))
        self.stack.enter_context(
            patch.object(jobs, "do_recompress_motion", side_effect=upload)
        )
        self.start_scheduler()
        response = self.client.post(
            "/api/enqueue",
            json={
                "ids": [VIDEO, PHOTO, MOTION],
                "names": {
                    aid: detail["originalFileName"] for aid, detail in details.items()
                },
                "medias": {VIDEO: "video", PHOTO: "image", MOTION: "motionphoto"},
                "motion_action": "recompress",
                "skip_codecs": "",
                "confirm": False,
                "min_savings": 0,
            },
        )
        self.assertEqual(response.json["added"], [VIDEO, PHOTO, MOTION])
        self.wait_idle()
        self.assertEqual(
            {aid: _jobs[aid]["status"] for aid in [VIDEO, PHOTO, MOTION]},
            {aid: "done" for aid in [VIDEO, PHOTO, MOTION]},
        )
        for stage in ["download", "encode", "upload"]:
            self.assertEqual(
                {aid for name, aid in stages if name == stage}, {VIDEO, PHOTO, MOTION}
            )
        self.assertEqual({job["status"] for job in db.db_load_jobs()}, {"done"})

    def test_snapshot_reports_all_active_jobs_and_private_fields_stay_private(self):
        first, second, release = threading.Event(), threading.Event(), threading.Event()
        self.stack.callback(release.set)
        scheduler.set_concurrency(2)
        for aid, event in [(VIDEO, first), (PHOTO, second)]:
            _jobs[aid] = jobs.new_job(aid, {})

            def prepare(e=event):
                def run():
                    e.set()
                    release.wait(timeout=2)
                    return False

                return scheduler.PreparedWork(run)

            scheduler.submit(
                scheduler.Task(aid, prepare, lambda _: None, lambda _: None)
            )
        self.start_scheduler()
        self.assertTrue(first.wait(1))
        self.assertTrue(second.wait(1))
        response = self.client.get("/api/jobs").json
        self.assertEqual(set(response["active_ids"]), {VIDEO, PHOTO})
        self.assertEqual(response["concurrent_jobs"], 2)
        self.assertFalse(
            any(key.startswith("_") for job in response["jobs"].values() for key in job)
        )
        release.set()
        self.wait_idle()
