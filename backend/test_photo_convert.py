"""Non-JPEG photos convert to JPEG or JPEG XL, keep their metadata and respect the cap."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backend import jobs, photo_convert
from backend.jobs import _image_skip_reason, new_job, process_image_job
from backend.state import _jobs, _queue_order

TOOLS = all(shutil.which(t) for t in ("ffmpeg", "ffprobe", "cjxl", "djxl", "jxlinfo", "exiftool"))
ASSET = "50000000-0000-4000-8000-000000000001"


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def exif(path, *tags):
    """Tag values as exiftool prints them with -s3, in the order asked."""
    return run("exiftool", "-s3", "-n", *[f"-{t}" for t in tags], str(path)).splitlines()


def probe(path):
    out = run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height,pix_fmt", "-of", "csv=p=0", str(path))
    width, height, pix_fmt = out.strip().split(",")
    return int(width), int(height), pix_fmt


def make_image(path, size="64x48", pix_fmt="rgb24", source="testsrc", extra=()):
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}=size={size}",
        "-frames:v", "1", "-pix_fmt", pix_fmt, *extra, str(path))


def tag(path, *assignments):
    run("exiftool", "-q", "-overwrite_original", *assignments, str(path))


def tiff_pages(path, *pages):
    """An uncompressed RGB TIFF with one IFD per (width, height, NewSubfileType)."""
    out, link = bytearray(b"II*\0\0\0\0\0"), 4
    for width, height, subfile in pages:
        strip = len(out)
        out += bytes([200, 30, 30]) * (width * height) + b"\0" * ((width * height * 3) % 2)
        bits = len(out)
        out += struct.pack("<HHH", 8, 8, 8)
        entries = [(254, 4, 1, subfile), (256, 4, 1, width), (257, 4, 1, height),
                   (258, 3, 3, bits), (259, 3, 1, 1), (262, 3, 1, 2), (273, 4, 1, strip),
                   (277, 3, 1, 3), (278, 4, 1, height), (279, 4, 1, width * height * 3)]
        struct.pack_into("<I", out, link, len(out))
        out += struct.pack("<H", len(entries))
        for tag_id, kind, count, value in entries:
            out += struct.pack("<HHIHH" if kind == 3 and count == 1 else "<HHII",
                               tag_id, kind, count, value, *([0] if kind == 3 and count == 1 else []))
        link = len(out)
        out += b"\0\0\0\0"
    Path(path).write_bytes(bytes(out))


def palette_png(path, size=32):
    """A palette PNG whose second colour is fully transparent through tRNS."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + bytes((x + y) % 2 for x in range(size)) for y in range(size))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n"
                           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 3, 0, 0, 0))
                           + chunk(b"PLTE", bytes([255, 0, 0, 0, 0, 255]))
                           + chunk(b"tRNS", bytes([255, 0]))
                           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


@unittest.skipUnless(TOOLS, "needs ffmpeg, libjxl-tools and exiftool")
class ConversionJobTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.work = self.dir / "work"
        self.stack.enter_context(patch("backend.jobs.WORK_DIR", str(self.work)))
        for name in ("emit_job_update", "broadcast_queue_update", "append_csv_log"):
            self.stack.enter_context(patch(f"backend.jobs.{name}"))
        self.replace = self.stack.enter_context(patch("backend.jobs.do_replace"))
        self.download = self.stack.enter_context(patch(
            "backend.jobs.download_original", side_effect=self.copy_source))
        self.source = None
        self.addCleanup(lambda: _jobs.pop(ASSET, None))
        self.addCleanup(lambda: _queue_order.remove(ASSET) if ASSET in _queue_order else None)

    def copy_source(self, env, asset_id, dest, **kwargs):
        shutil.copyfile(self.source, dest)
        return True, ""

    def convert(self, source, **overrides):
        self.source = source
        params = {"media": "image", "name": Path(source).name, "photo_format": "jxl",
                  "photo_jxl_distance": 1, "min_savings": 0, "threads": 1,
                  "backup_dir": str(self.dir / "backup"), **overrides}
        _jobs[ASSET] = new_job(ASSET, params)
        process_image_job({"url": "https://immich.test", "api_key": "k"}, ASSET, params)
        return _jobs[ASSET]

    def converted(self, job):
        self.assertEqual(job["status"], "review", job["log"])
        out = Path(job["_out_path"])
        self.assertTrue(out.is_file())
        return out

    def tiff(self, name="photo.tif", **kwargs):
        path = self.dir / name
        make_image(path, **kwargs)
        width, height = kwargs.get("size", "64x48").split("x")
        tag(path, "-DateTimeOriginal=2019:05:04 10:11:12", "-GPSLatitude=38.7",
            "-GPSLatitudeRef=N", "-Orientation#=6", f"-ExifIFD:ExifImageWidth={width}",
            f"-ExifIFD:ExifImageHeight={height}")
        return path

    def test_tiff_to_lossy_jxl_keeps_exif_and_displays_upright(self):
        job = self.convert(self.tiff())
        out = self.converted(job)
        self.assertEqual(job["_upload_name"], "photo.jxl")
        date, lat, orientation = exif(out, "DateTimeOriginal", "GPSLatitude", "Orientation")
        self.assertEqual(date, "2019:05:04 10:11:12")
        self.assertAlmostEqual(float(lat), 38.7)
        # The pixels are rotated upright once, so no viewer rotates them again.
        self.assertEqual(orientation, "1")
        self.assertEqual(probe(job["_out_path"] + ".preview.png")[:2], (48, 64))
        self.assertNotIn("Orientation", run("jxlinfo", str(out)))
        self.assertEqual(exif(out, "ExifImageWidth", "ExifImageHeight"), ["48", "64"])
        self.assertFalse(list(self.work.glob("*decoded*")))

    def p3_tiff(self):
        plain, p3jxl, p3png = self.dir / "plain.png", self.dir / "p3.jxl", self.dir / "p3.png"
        make_image(plain)
        run("cjxl", str(plain), str(p3jxl), "-d", "0", "-x", "color_space=RGB_D65_DCI_Rel_SRG")
        run("djxl", str(p3jxl), str(p3png))
        icc = self.dir / "p3.icc"
        icc.write_bytes(subprocess.run(["exiftool", "-icc_profile", "-b", str(p3png)],
                                       check=True, capture_output=True).stdout)
        source = self.tiff()
        tag(source, f"-icc_profile<={icc}")
        return source

    def test_icc_profile_survives(self):
        out = self.converted(self.convert(self.p3_tiff()))
        self.assertIn("P3 primaries", run("jxlinfo", str(out)))
        out = self.converted(self.convert(self.p3_tiff(), photo_format="jpeg"))
        self.assertEqual(exif(out, "ICC_Profile:ProfileDescription"), ["RGB_D65_DCI_Rel_SRG"])

    def test_lossless_jxl_to_lossy(self):
        jpeg = self.dir / "photo.jpg"
        make_image(jpeg, size="256x192", pix_fmt="yuvj444p", extra=("-q:v", "2"))
        tag(jpeg, "-DateTimeOriginal=2020:01:02 03:04:05", "-Orientation#=6")
        source = self.dir / "photo.jxl"
        run("cjxl", str(jpeg), str(source), "--lossless_jpeg=1")
        self.assertIn("Brotli-compressed Exif", run("jxlinfo", str(source)))
        job = self.convert(source, photo_jxl_distance=2)
        out = self.converted(job)
        self.assertLess(out.stat().st_size, source.stat().st_size)
        self.assertEqual(exif(out, "DateTimeOriginal", "Orientation"),
                         ["2020:01:02 03:04:05", "1"])
        self.assertEqual(probe(job["_out_path"] + ".preview.png")[:2], (192, 256))

    def test_capped_jpeg_matches_its_dimension_tags(self):
        source = self.dir / "big.jpg"
        make_image(source, size="4000x3000", pix_fmt="yuvj420p", source="testsrc2",
                   extra=("-q:v", "2"))
        tag(source, "-DateTimeOriginal=2021:02:03 04:05:06", "-ExifIFD:ExifImageWidth=4000",
            "-ExifIFD:ExifImageHeight=3000")
        out = self.converted(self.convert(source, photo_format="jpeg", photo_max_mp=3))
        self.assertEqual(probe(out), (2000, 1500, "yuvj420p"))
        self.assertEqual(exif(out, "ExifImageWidth", "ExifImageHeight", "DateTimeOriginal"),
                         ["2000", "1500", "2021:02:03 04:05:06"])

    def test_jpeg_under_the_cap_keeps_the_direct_path(self):
        source = self.dir / "small.jpg"
        make_image(source, size="640x480", pix_fmt="yuvj420p", extra=("-q:v", "2"))
        with patch.object(photo_convert, "decode_to_png") as decode:
            self.converted(self.convert(source, photo_format="jpeg", photo_max_mp=3))
        decode.assert_not_called()

    def test_jpeg_chroma(self):
        cases = [("yuvj444p", "source", "yuvj444p"), ("yuvj444p", "420", "yuvj420p"),
                 ("yuvj420p", "source", "yuvj420p"), ("yuvj420p", "444", "yuvj444p")]
        for source_fmt, chroma, expected in cases:
            with self.subTest(source=source_fmt, chroma=chroma):
                source = self.dir / f"{source_fmt}.jpg"
                make_image(source, size="320x240", pix_fmt=source_fmt, extra=("-q:v", "2"))
                out = self.converted(self.convert(source, photo_format="jpeg",
                                                  photo_jpeg_chroma=chroma))
                self.assertEqual(probe(out)[2], expected)

    def test_tiff_to_jpeg_keeps_full_chroma_and_takes_the_jpeg_name(self):
        job = self.convert(self.tiff(size="320x240"), photo_format="jpeg")
        out = self.converted(job)
        self.assertEqual(job["_upload_name"], "photo.jpg")
        self.assertEqual(probe(out)[2], "yuvj444p")
        self.assertEqual(exif(out, "DateTimeOriginal", "Orientation"),
                         ["2019:05:04 10:11:12", "1"])

    def test_missing_exiftool_skips_before_download(self):
        real_which = shutil.which
        with patch("backend.jobs.shutil.which",
                   side_effect=lambda name: None if name == "exiftool" else real_which(name)):
            job = self.convert(self.tiff())
        self.assertEqual((job["status"], job["skip_reason"]), ("skipped", "no exiftool"))
        self.download.assert_not_called()
        self.replace.assert_not_called()

    def test_transparent_png_is_not_flattened_to_jpeg(self):
        for name, make in (("clear.png", lambda p: make_image(
                               p, size="320x240", pix_fmt="rgba",
                               extra=("-vf", "format=rgba,geq=r=255:g=0:b=0:a=X"))),
                           ("palette.png", palette_png)):
            with self.subTest(name=name):
                source = self.dir / name
                make(source)
                job = self.convert(source, photo_format="jpeg")
                self.assertEqual(job["status"], "error")
                self.assertIn("transparency", job["log"])
                # JPEG XL keeps alpha. A tiny PNG may stay smaller than the JXL.
                self.assertIn(self.convert(source)["status"], ("review", "skipped"))

    def test_opaque_alpha_channel_converts_to_jpeg(self):
        rgba, webp = self.dir / "opaque.png", self.dir / "opaque.webp"
        make_image(rgba, size="320x240", pix_fmt="rgba")
        make_image(webp, size="320x240", pix_fmt="rgba", extra=("-c:v", "libwebp", "-lossless", "1"))
        self.assertEqual(probe(webp)[2], "argb")
        for source in (rgba, webp):
            with self.subTest(source=source.name):
                job = self.convert(source, photo_format="jpeg")
                # testsrc compresses so well that a lossless source may stay smaller.
                self.assertIn(job["status"], ("review", "skipped"), job["log"])
                self.assertNotIn("transparency", job["log"])

    def bmp(self, name, pix_fmt, alpha="255"):
        path = self.dir / name
        make_image(path, size="320x240", pix_fmt=pix_fmt,
                   extra=("-vf", f"format=rgba,geq=r=X:g=Y:b=128:a={alpha}", "-pix_fmt", pix_fmt))
        return path

    def test_bmp_converts_to_jxl_and_jpeg(self):
        source = self.bmp("scan.bmp", "bgr24")
        for photo_format, ext in (("jxl", "jxl"), ("jpeg", "jpg")):
            with self.subTest(photo_format=photo_format):
                job = self.convert(source, photo_format=photo_format)
                self.converted(job)
                self.assertEqual(job["_upload_name"], f"scan.{ext}")

    def test_transparent_bmp_keeps_alpha_in_jxl_and_is_refused_for_jpeg(self):
        source = self.bmp("clear.bmp", "bgra", alpha="X")
        self.assertEqual(probe(source)[2], "bgra")
        out = self.converted(self.convert(source))
        self.assertRegex(run("jxlinfo", str(out)).lower(), r"alpha|rgba")  # jxlinfo 0.7 prints "RGBA"
        job = self.convert(source, photo_format="jpeg")
        self.assertEqual(job["status"], "error")
        self.assertIn("transparency", job["log"])

    def test_opaque_32_bit_bmp_converts_to_jpeg(self):
        job = self.convert(self.bmp("opaque.bmp", "bgra"), photo_format="jpeg")
        self.converted(job)
        self.assertNotIn("transparency", job["log"])

    def test_animations_and_extra_pages_are_skipped(self):
        apng, gif = self.dir / "moving.png", self.dir / "moving.gif"
        for path in (apng, gif):
            run("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5",
                "-frames:v", "3", *(["-f", "apng"] if path == apng else []), str(path))
        jxl = self.dir / "moving.jxl"
        run("cjxl", str(gif), str(jxl))
        pages = self.dir / "pages.tif"
        tiff_pages(pages, (64, 48, 0), (64, 48, 0))
        for source, reason in ((apng, "animated"), (jxl, "animated"), (pages, "multi-page")):
            with self.subTest(source=source.name):
                job = self.convert(source)
                self.assertEqual((job["status"], job["skip_reason"]), ("skipped", reason))
                self.assertIsNone(job["_out_path"])
        self.replace.assert_not_called()

    def test_tiff_with_a_thumbnail_page_converts(self):
        source = self.dir / "thumb.tif"
        tiff_pages(source, (320, 240, 0), (32, 24, 1))
        self.converted(self.convert(source))

    def test_xmp_and_exif_dates_are_compared_in_their_own_group(self):
        source = self.tiff(size="320x240")
        tag(source, "-XMP-exif:DateTimeOriginal=2019:05:04 10:11:12+02:00",
            "-XMP-exif:GPSLatitude=38.7", "-XMP-exif:GPSLatitudeRef=N")
        out = self.converted(self.convert(source, photo_format="jpeg"))
        self.assertEqual(exif(out, "EXIF:DateTimeOriginal"), ["2019:05:04 10:11:12"])

    def test_path_taken_is_recorded_on_the_job(self):
        source = self.dir / "big.jpg"
        make_image(source, size="2000x1500", pix_fmt="yuvj420p", extra=("-q:v", "2"))
        self.assertFalse(self.converted_job(source)["pixel_path"])
        self.assertTrue(self.converted_job(source, photo_max_mp=1)["pixel_path"])

    def converted_job(self, source, **overrides):
        job = self.convert(source, photo_jxl_distance=0, **overrides)
        self.converted(job)
        return job

    def test_metadata_copy_failure_fails_the_job(self):
        with patch.object(photo_convert, "copy_metadata", return_value=(False, "lost the date")):
            job = self.convert(self.tiff(), confirm=False)
        self.assertEqual(job["status"], "error")
        self.assertIn("lost the date", job["log"])
        self.replace.assert_not_called()
        self.assertFalse(list(self.work.glob(f"{ASSET}_out*")))

    @unittest.skipUnless(os.environ.get("RAW_SAMPLE"), "set RAW_SAMPLE to RAW files")
    def test_raw_converts_with_dcraw_emu(self):
        for sample in os.environ["RAW_SAMPLE"].split(os.pathsep):
            with self.subTest(sample=sample):
                self.assertEqual(photo_convert.decoder_for(Path(sample).suffix[1:]), "dcraw_emu")
                date = exif(sample, "DateTimeOriginal")
                portrait = self.dir / f"portrait{Path(sample).suffix}"
                shutil.copyfile(sample, portrait)
                tag(portrait, "-Orientation#=6")
                out = self.converted(self.convert(str(portrait), compress_raw=True,
                                                  photo_max_mp=2))
                self.assertEqual(exif(out, "DateTimeOriginal", "Orientation"), [*date, "1"])
                width, height, _ = probe(str(out) + ".preview.png")
                self.assertLessEqual(width * height, 2_000_000)
                self.assertLess(width, height)
                self.assertIn("16-bit", run("jxlinfo", str(out)))


@unittest.skipUnless(TOOLS, "needs ffmpeg, libjxl-tools and exiftool")
class DecodeTest(unittest.TestCase):
    def test_cap_downscales_only_larger_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "big.png", Path(tmp) / "out.png"
            make_image(src, size="4000x3000")
            for cap, expected in ((3, (2000, 1500)), (20, (4000, 3000)), (None, (4000, 3000))):
                with self.subTest(cap=cap):
                    ok, error = photo_convert.decode_to_png(str(src), "png", str(out), cap, ASSET)
                    self.assertTrue(ok, error)
                    self.assertEqual(probe(out), (*expected, "rgb24"))

    def test_every_orientation_ends_upright(self):
        # How each EXIF orientation maps a displayed pixel (x, y) to the stored one.
        stored = {1: lambda x, y: (x, y), 2: lambda x, y: (W - 1 - x, y),
                  3: lambda x, y: (W - 1 - x, H - 1 - y), 4: lambda x, y: (x, H - 1 - y),
                  5: lambda x, y: (y, x), 6: lambda x, y: (y, H - 1 - x),
                  7: lambda x, y: (W - 1 - y, H - 1 - x), 8: lambda x, y: (W - 1 - y, x)}
        W, H = 6, 4

        def pixels(path):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-noautorotate", "-i", str(path),
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 check=True, capture_output=True).stdout
            return [raw[i:i + 3] for i in range(0, len(raw), 3)]
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "photo.tif", Path(tmp) / "out.png"
            run("ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                f"nullsrc=s={W}x{H},geq=r=X*40:g=Y*60:b=128", "-frames:v", "1",
                "-pix_fmt", "rgb24", str(src))
            source = pixels(src)
            for orientation, where in stored.items():
                with self.subTest(orientation=orientation):
                    tag(src, f"-Orientation#={orientation}")
                    ok, error = photo_convert.decode_to_png(str(src), "tif", str(out), None, ASSET)
                    self.assertTrue(ok, error)
                    width, height = (H, W) if orientation > 4 else (W, H)
                    self.assertEqual(probe(out)[:2], (width, height))
                    expected = [source[sy * W + sx] for y in range(height) for x in range(width)
                                for sx, sy in [where(x, y)]]
                    self.assertEqual(pixels(out), expected)

    def test_sixteen_bit_source_stays_sixteen_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "deep.tif", Path(tmp) / "out.png"
            make_image(src, pix_fmt="rgb48le")
            ok, error = photo_convert.decode_to_png(str(src), "tif", str(out), None, ASSET)
            self.assertTrue(ok, error)
            self.assertEqual(probe(out)[2], "rgb48be")

    def test_copy_fails_when_the_date_is_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "a.tif", Path(tmp) / "b.jpg"
            make_image(src)
            make_image(dst)
            tag(src, "-DateTimeOriginal=2019:05:04 10:11:12")
            with patch.object(photo_convert, "_run", return_value=(0, "")):
                ok, error = photo_convert.copy_metadata(str(src), str(dst), ASSET)
            self.assertFalse(ok)
            self.assertIn("capture date", error)

    def test_copy_fails_when_gps_is_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "a.tif", Path(tmp) / "b.jpg"
            make_image(src)
            make_image(dst)
            tag(src, "-GPSLatitude=38.7", "-GPSLatitudeRef=N")
            with patch.object(photo_convert, "_run", return_value=(0, "")):
                ok, error = photo_convert.copy_metadata(str(src), str(dst), ASSET)
            self.assertFalse(ok)
            self.assertIn("GPS", error)

    def test_unreadable_source_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "a.tif", Path(tmp) / "b.jpg"
            make_image(src)
            make_image(dst)
            for failure in (subprocess.TimeoutExpired("exiftool", 60),
                            subprocess.CompletedProcess([], 1, "", "Error")):
                with self.subTest(failure=type(failure).__name__), patch.object(
                        photo_convert.subprocess, "run",
                        **({"side_effect": failure} if isinstance(failure, Exception)
                           else {"return_value": failure})), \
                        patch.object(photo_convert, "_run") as run_tool:
                    ok, error = photo_convert.copy_metadata(str(src), str(dst), ASSET)
                    self.assertFalse(ok)
                    self.assertIn("read the metadata", error)
                    run_tool.assert_not_called()

    def test_unreadable_compressed_metadata_fails_before_writing(self):
        warning = ({}, ["Install IO::Uncompress::Brotli to decode Brotli-compressed metadata"])
        with patch.object(photo_convert, "_read_tags", return_value=warning), \
                patch.object(photo_convert, "_run") as run_tool:
            ok, error = photo_convert.copy_metadata("a.jxl", "b.jxl", ASSET)
        self.assertFalse(ok)
        self.assertIn("libio-compress-brotli-perl", error)
        run_tool.assert_not_called()


class DiskNeedTest(unittest.TestCase):
    """Admission counts the decoded PNG, and the decoder's own copy for RAW."""

    def work_need(self, name, width=None, height=None, **params):
        exif = {"exifImageWidth": width, "exifImageHeight": height} if width else {}
        params = {"media": "image", "name": name, "replace": False, "photo_format": "jpeg",
                  "compress_raw": True, **params}
        with patch("backend.photo_convert.shutil.which", return_value="/usr/bin/tool"):
            (need,) = jobs._disk_needs(ASSET, params, {"exifInfo": exif}, 1_000_000, "/b")
        return need.amount

    def test_png_path_counts_sixteen_bit_pixels(self):
        direct = self.work_need("a.jpg", 4000, 3000)
        self.assertEqual(direct, 3_000_000)
        self.assertEqual(self.work_need("a.tif", 4000, 3000), direct + 12_000_000 * 6)
        self.assertEqual(self.work_need("a.cr2", 4000, 3000), direct + 12_000_000 * 12)
        self.assertEqual(self.work_need("a.jpg", 4000, 3000, photo_max_mp=3),
                         direct + 12_000_000 * 6)
        self.assertEqual(self.work_need("a.jpg", 4000, 3000, photo_max_mp=20), direct)

    def test_unknown_dimensions_fall_back_to_the_file_size(self):
        self.assertEqual(self.work_need("a.tif"), 3_000_000 + 30_000_000)

    def test_jxl_preview_counts_sixteen_bit_rgba(self):
        self.assertEqual(self.work_need("a.tif", 4000, 3000, photo_format="jxl"),
                         3_000_000 + 12_000_000 * 8 + 1024**2 + 12_000_000 * 6)


class PixelPathTest(unittest.TestCase):
    def test_needs_pixel_path(self):
        cases = [
            ("jpg", None, None, False),
            ("JPEG", 12.0, 0, False),
            ("jpg", 12.0, 20, False),
            ("jpg", 12.0, 3, True),
            ("jpg", None, 3, True),
            ("jpg", 2.0, 3, False),
            ("tif", None, None, True),
            ("png", 1.0, 3, True),
            ("jxl", None, None, True),
            ("dng", None, None, True),
        ]
        for fmt, src_mp, max_mp, expected in cases:
            with self.subTest(fmt=fmt, src_mp=src_mp, max_mp=max_mp):
                self.assertEqual(photo_convert.needs_pixel_path(fmt, src_mp, max_mp), expected)

    def test_skip_reasons(self):
        with patch("backend.photo_convert.shutil.which", return_value="/usr/bin/tool"), \
                patch("backend.jobs.shutil.which", return_value="/usr/bin/tool"):
            self.assertEqual(_image_skip_reason("TIFF", {"photo_format": "jxl"}), ("", ""))
            self.assertEqual(_image_skip_reason("HEIC", {"photo_format": "jxl"})[1],
                             "no decoder")
            self.assertEqual(_image_skip_reason("DNG", {})[1], "RAW off")
            self.assertEqual(_image_skip_reason("DNG", {"compress_raw": True}), ("", ""))
        with patch("backend.photo_convert.shutil.which",
                   side_effect=lambda t: None if t == "dcraw_emu" else "/usr/bin/sips"):
            self.assertEqual(photo_convert.decoder_for("cr2"), "sips")
        with patch("backend.photo_convert.shutil.which", return_value=None):
            self.assertIsNone(photo_convert.decoder_for("tif"))


if __name__ == "__main__":
    unittest.main()


class RawCapabilityTest(unittest.TestCase):
    def test_capabilities_report_raw_decoding(self):
        from flask import Flask

        from backend import routes
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        client = app.test_client()
        cases = (({"dcraw_emu"}, True), ({"sips"}, True), (set(), False))
        for tools, expected in cases:
            with self.subTest(tools=tools), \
                    patch.object(routes.shutil, "which", side_effect=lambda name, tools=tools: f"/bin/{name}" if name in tools else None), \
                    patch.object(routes, "available_encoders", return_value=[]):
                self.assertIs(client.get("/api/capabilities").get_json()["raw"], expected)
