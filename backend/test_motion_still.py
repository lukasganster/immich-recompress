"""A still with motion: the embedded clip is cut off before encoding, and the
replacement keeps or removes the linked motion video as the setting says."""

import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backend import jobs, photo_convert
from backend.jobs import do_replace, new_job
from backend.state import _jobs
from backend.test_photo_convert import (
    TOOLS,
    ConversionHarness,
    exif,
    make_image,
    run,
    tag,
)

VIDEO = "60000000-0000-4000-8000-000000000002"
LINKED = {"livePhotoVideoId": VIDEO}


def make_clip(path):
    run("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "1",
        "-c:v", "mpeg4", "-f", "mp4", str(path))
    return Path(path).read_bytes()


def pixels(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
                           "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout


@unittest.skipUnless(TOOLS, "needs ffmpeg, libjxl-tools and exiftool")
class MotionStillJobTest(ConversionHarness):
    def motion_photo(self, layout="micro", offset_shift=0):
        still, source = self.dir / "still.jpg", self.dir / f"MVIMG_{layout}.jpg"
        make_image(still, size="320x240", pix_fmt="yuvj420p", extra=("-q:v", "2"))
        tag(still, "-DateTimeOriginal=2018:04:24 23:50:13")
        clip = make_clip(self.dir / "clip.mp4")
        source.write_bytes(still.read_bytes())
        if layout == "micro":
            tag(source, "-XMP-GCamera:MicroVideo=1", "-XMP-GCamera:MicroVideoVersion=1",
                f"-XMP-GCamera:MicroVideoOffset={len(clip) + offset_shift}")
        else:
            items = "{Item={Semantic=Primary,Mime=image/jpeg}},"
            if layout == "gainmap":
                items += "{Item={Semantic=GainMap,Mime=image/jpeg,Length=10}},"
            items += f"{{Item={{Semantic=MotionPhoto,Mime=video/mp4,Length={len(clip)},Padding=0}}}}"
            try:
                tag(source, "-XMP-GCamera:MotionPhoto=1",
                    f"-XMP-GContainer:ContainerDirectory=[{items}]")
            except subprocess.CalledProcessError:
                self.skipTest("this exiftool can't write the Container directory")
        with source.open("ab") as fh:
            fh.write(clip)
        return still, source

    def test_motion_photo_converts_to_lossless_jxl_without_its_clip(self):
        for layout in ("micro", "container"):
            with self.subTest(layout=layout):
                still, source = self.motion_photo(layout)
                job = self.convert(source, photo_jxl_distance=0, motion_action="keep",
                                   _source_detail=LINKED)
                out = self.converted(job)
                self.assertFalse(job["pixel_path"])
                self.assertEqual(job["motion_note"], "Motion video kept (relinked)")
                self.assertLess(job["old_size"], source.stat().st_size)
                tags = run("exiftool", "-s", "-XMP-GCamera:all", "-XMP-GContainer:all", str(out))
                self.assertNotIn("MicroVideo", tags)
                self.assertNotIn("MotionPhoto", tags)
                self.assertNotIn("Container", tags)
                self.assertEqual(exif(out, "DateTimeOriginal"), ["2018:04:24 23:50:13"])
                restored = self.dir / "restored.jpg"
                run("djxl", str(out), str(restored))
                self.assertNotIn(b"ftyp", restored.read_bytes())
                self.assertEqual(pixels(restored), pixels(still))
                self.assertTrue(source.exists())
                self.assertFalse(list(self.work.glob("*_still*")))

    def test_leftover_motion_tags_fail_the_job(self):
        _, source = self.motion_photo()

        def cut_only(src, dst, length, asset_id):
            Path(dst).write_bytes(Path(src).read_bytes()[:-length])
            return True, ""
        with patch.object(photo_convert, "strip_motion", side_effect=cut_only):
            job = self.convert(source, photo_jxl_distance=0, motion_action="keep",
                               _source_detail=LINKED)
        self.assertEqual(job["status"], "error")
        self.assertIn("Motion tags remain", job["log"])
        self.replace.assert_not_called()

    def test_backup_keeps_the_clip(self):
        _, source = self.motion_photo()
        job = self.convert(source, photo_jxl_distance=0, motion_action="remove",
                           _source_detail=LINKED)
        self.converted(job)
        self.assertEqual(job["motion_note"], "Motion video removed (setting: Remove)")
        backups = list((self.dir / "backup").iterdir())
        self.assertEqual([b.read_bytes() for b in backups], [source.read_bytes()])

    def test_unknown_layout_is_skipped(self):
        for layout, shift, reason in (("micro", 7, "unknown motion layout"),
                                      ("gainmap", 0, "Ultra HDR")):
            with self.subTest(layout=layout):
                _, source = self.motion_photo(layout, offset_shift=shift)
                job = self.convert(source, motion_action="keep", _source_detail=LINKED)
                self.assertEqual((job["status"], job["skip_reason"]), ("skipped", reason))

    def test_clip_immich_has_not_extracted_is_only_dropped_on_remove(self):
        _, source = self.motion_photo()
        job = self.convert(source, motion_action="keep", _source_detail={})
        self.assertEqual((job["status"], job["skip_reason"]), ("skipped", "motion not extracted"))
        job = self.convert(source, photo_jxl_distance=0, motion_action="remove", _source_detail={})
        self.converted(job)
        self.assertEqual(job["motion_note"], "Embedded motion clip removed (setting: Remove)")

    def test_savings_are_measured_against_the_still(self):
        _, source = self.motion_photo()
        clip = (self.dir / "clip.mp4").stat().st_size
        job = self.convert(source, photo_jxl_distance=0, motion_action="remove",
                           _source_detail=LINKED)
        out = self.converted(job)
        self.assertLessEqual(job["old_size"], source.stat().st_size - clip)
        self.assertEqual(job["savings"], job["old_size"] - out.stat().st_size)


@unittest.skipUnless(TOOLS, "needs ffmpeg, libjxl-tools and exiftool")
class UltraHdrSkipTest(ConversionHarness):
    """A primary JPEG followed by a gain map JPEG, and optionally a motion clip."""

    def ultra_hdr(self, motion=False):
        primary, gain_map = self.dir / "primary.jpg", self.dir / "gainmap.jpg"
        source = self.dir / ("PXL.MP.jpg" if motion else "PXL.jpg")
        make_image(primary, size="320x240", pix_fmt="yuvj420p", extra=("-q:v", "2"))
        make_image(gain_map, size="160x120", pix_fmt="gray", extra=("-q:v", "2"))
        tag(gain_map, "-XMP-hdrgm:Version=1.0", "-XMP-hdrgm:GainMapMax=2.0")
        clip = make_clip(self.dir / "clip.mp4") if motion else b""
        source.write_bytes(primary.read_bytes())
        items = ("{Item={Semantic=Primary,Mime=image/jpeg}},{Item={Semantic=GainMap,"
                 f"Mime=image/jpeg,Length={gain_map.stat().st_size}}}}}")
        assignments = ["-XMP-hdrgm:Version=1.0"]
        if motion:
            items += f",{{Item={{Semantic=MotionPhoto,Mime=video/mp4,Length={len(clip)}}}}}"
            assignments.append("-XMP-GCamera:MotionPhoto=1")
        # Older exiftool can't write the directory; the hdrgm XMP alone still marks it.
        try:
            tag(source, *assignments, f"-XMP-GContainer:ContainerDirectory=[{items}]")
        except subprocess.CalledProcessError:
            tag(source, *assignments)
        with source.open("ab") as fh:
            fh.write(gain_map.read_bytes() + clip)
        return source

    def test_gain_map_photos_are_skipped_on_every_path(self):
        settings = ({"photo_jxl_distance": 0}, {"photo_jxl_distance": 1},
                    {"photo_format": "jpeg"}, {"photo_jxl_distance": 0, "photo_max_mp": 0.01})
        with patch.object(jobs, "_encode_jxl_image") as encode_jxl, \
                patch.object(jobs, "compress_image_to_target") as encode_jpeg, \
                patch.object(photo_convert, "decode_to_png") as decode:
            for motion in (False, True):
                for setting in settings:
                    with self.subTest(motion=motion, **setting):
                        job = self.convert(self.ultra_hdr(motion), motion_action="keep",
                                           _source_detail=LINKED, **setting)
                        self.assertEqual((job["status"], job["skip_reason"]),
                                         ("skipped", "Ultra HDR"))
                        self.assertIn("gain map", job["log"])
                        self.assertIsNone(job["_out_path"])
        encode_jxl.assert_not_called()
        encode_jpeg.assert_not_called()
        decode.assert_not_called()
        self.assertFalse(list(self.work.glob("*")))

    def test_plain_jpeg_still_converts(self):
        source = self.dir / "plain.jpg"
        make_image(source, size="320x240", pix_fmt="yuvj420p", extra=("-q:v", "2"))
        self.converted(self.convert(source, photo_jxl_distance=0))


class MotionReplaceTest(unittest.TestCase):
    """Order of the Immich calls when a still with a linked motion video is replaced."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.out = directory / "out.jxl"
        self.out.write_bytes(b"jxl")
        self.calls = []
        self.link_ok = True
        self.detail = {"livePhotoVideoId": VIDEO}

        def record(name, result):
            def call(env, *args, **kwargs):
                self.calls.append((name, *args, *kwargs.values()))
                return result() if callable(result) else result
            return call
        for name, result in (
            ("fetch_asset", lambda: self.detail),
            ("get_asset_meta", {"exifInfo": {"dateTimeOriginal": "2018-04-24T23:50:13Z"}}),
            ("upload_new_asset", ("new", "created")),
            ("copy_asset_metadata", True),
            ("update_asset", True),
            ("set_live_photo_link", lambda: self.link_ok),
            ("trash_asset", True),
        ):
            self.stack.enter_context(patch.object(jobs, name, side_effect=record(name, result)))
        self.stack.enter_context(patch.object(jobs, "capture_metadata_fields",
                                              return_value={"dateTimeOriginal": "x"}))
        for name in ("emit_job_update", "append_csv_log", "persist_if_terminal",
                     "broadcast_queue_update", "discard_backup"):
            self.stack.enter_context(patch.object(jobs, name))
        self.addCleanup(lambda: _jobs.pop("old", None))

    def replace(self, action):
        self.out.write_bytes(b"jxl")
        _jobs["old"] = new_job("old", {"media": "image", "motion_action": action})
        ok = do_replace({"url": "https://immich.test"}, "old", "MVIMG.jxl", str(self.out),
                        9_000_000, 2_000_000, 7_000_000, "JPG", mime="image/jxl")
        return ok, [c[0] if c[0] not in ("set_live_photo_link", "trash_asset") else c
                    for c in self.calls]

    def test_keep_links_the_new_still_before_trashing_the_old(self):
        for action in ("keep", "recompress"):
            with self.subTest(action=action):
                self.calls.clear()
                ok, calls = self.replace(action)
                self.assertTrue(ok)
                self.assertEqual(calls, ["fetch_asset", "get_asset_meta", "upload_new_asset",
                                         "copy_asset_metadata", "update_asset",
                                         ("set_live_photo_link", "new", VIDEO),
                                         ("trash_asset", "old")])
                self.assertIn("Motion video kept (relinked)", _jobs["old"]["log"])

    def test_remove_trashes_the_motion_video_after_the_old_still(self):
        ok, calls = self.replace("remove")
        self.assertTrue(ok)
        self.assertEqual(calls[-2:], [("trash_asset", "old"), ("trash_asset", VIDEO)])
        self.assertNotIn("set_live_photo_link", [c[0] for c in self.calls])
        self.assertIn("Motion video removed", _jobs["old"]["log"])

    def test_failed_relink_keeps_the_old_still(self):
        self.link_ok = False
        ok, _ = self.replace("keep")
        self.assertFalse(ok)
        self.assertEqual(_jobs["old"]["status"], "error")
        self.assertNotIn("trash_asset", [c[0] for c in self.calls])

    def test_unreadable_link_stops_before_upload(self):
        self.detail = {}
        ok, calls = self.replace("keep")
        self.assertFalse(ok)
        self.assertEqual(calls, ["fetch_asset"])

    def test_still_without_motion_is_replaced_as_before(self):
        self.detail = {"id": "old"}
        ok, calls = self.replace("keep")
        self.assertTrue(ok)
        self.assertEqual(calls[-1], ("trash_asset", "old"))
        self.assertNotIn("set_live_photo_link", [c[0] for c in self.calls])


if __name__ == "__main__":
    unittest.main()
