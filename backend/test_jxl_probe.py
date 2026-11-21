"""Lossless JPEG XL classification from a prefix of the original."""

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from backend import db, jxl_probe, routes

ENV = {"url": "https://immich.test", "api_keys": ["owner"], "api_key": "owner"}
HAS_TOOLS = all(shutil.which(tool) for tool in ("cjxl", "jxlinfo", "ffmpeg"))


def run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def jxlinfo(path):
    result = subprocess.run(["jxlinfo", path], capture_output=True, text=True, check=False)
    return result.stdout + result.stderr


class TempDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for target, name, value in ((db, "DB_PATH", os.path.join(self.tmp, "t.db")),
                                    (jxl_probe, "WORK_DIR", self.tmp)):
            mock = patch.object(target, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        db.db_init()


@unittest.skipUnless(HAS_TOOLS, "needs cjxl, jxlinfo and ffmpeg")
class JxlFixtureTest(TempDbTest):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        d = cls.dir
        for name, size in (("small", "64x48"), ("noise", "1024x768")):
            run("ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                f"nullsrc=s={size},geq=random(1)*255:128:128", "-frames:v", "1",
                "-pix_fmt", "rgb24", f"{d}/{name}.png")
            run("cjxl", "--quiet", f"{d}/{name}.png", f"{d}/{name}_d0.jxl", "-d", "0")
            run("cjxl", "--quiet", f"{d}/{name}.png", f"{d}/{name}_d1.jxl", "-d", "1")
        run("ffmpeg", "-loglevel", "error", "-i", f"{d}/small.png", f"{d}/small.jpg")
        run("cjxl", "--quiet", f"{d}/small.jpg", f"{d}/small_jpeg.jxl", "--lossless_jpeg=1")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_parse_lossless_and_lossy(self):
        self.assertIs(jxl_probe.parse_jxlinfo(jxlinfo(self.path("small_d0.jxl"))), True)
        self.assertIs(jxl_probe.parse_jxlinfo(jxlinfo(self.path("small_d1.jxl"))), False)
        self.assertIs(jxl_probe.parse_jxlinfo(jxlinfo(self.path("small_jpeg.jxl"))), True)
        self.assertIsNone(jxl_probe.parse_jxlinfo("Error reading file"))

    def test_truncated_prefix_classifies(self):
        for name, expected in (("noise_d0.jxl", True), ("noise_d1.jxl", False)):
            with self.subTest(name=name):
                with open(self.path(name), "rb") as fh:
                    data = fh.read()
                self.assertGreater(len(data), 64 * 1024)
                cut = self.path("cut_" + name)
                with open(cut, "wb") as fh:
                    fh.write(data[:64 * 1024])
                self.assertIs(jxl_probe.parse_jxlinfo(jxlinfo(cut)), expected)

    def streamed(self, body, consumed):
        """A response that ignores Range and streams the whole body."""
        def iter_content(chunk_size=1):
            for start in range(0, len(body), chunk_size):
                chunk = body[start:start + chunk_size]
                consumed.append(len(chunk))
                yield chunk
        resp = MagicMock(status_code=200)
        resp.__enter__.return_value = resp
        resp.iter_content.side_effect = iter_content
        return resp

    def test_classify_reads_prefix_only(self):
        with open(self.path("noise_d1.jxl"), "rb") as fh:
            lossy = fh.read()
        cases = ((lossy + b"\0" * (5 * 1024 ** 2 - len(lossy)), False, 1),
                 (b"\0" * (5 * 1024 ** 2), None, 2))
        for body, expected, calls in cases:
            with self.subTest(expected=expected):
                consumed = []
                with patch.object(jxl_probe.requests, "get",
                                  side_effect=lambda *a, _b=body, _c=consumed, **k: self.streamed(_b, _c)) as get:
                    self.assertIs(jxl_probe.classify_jxl(ENV, "a", len(body)), expected)
                self.assertEqual(get.call_count, calls)
                self.assertTrue(get.call_args.kwargs["stream"])
                self.assertEqual(get.call_args_list[0].kwargs["headers"]["Range"], "bytes=0-65535")
                self.assertLessEqual(sum(consumed), 64 * 1024 * calls if expected is False else 64 * 1024 + 1024 ** 2)
        self.assertEqual(os.listdir(self.tmp), ["t.db"])


class CacheTest(TempDbTest):
    def test_cache_invalidates_on_size_change(self):
        db.save_jxl_lossless("a", 100, True)
        self.assertIs(db.jxl_is_lossless_cached("a", 100), True)
        self.assertIsNone(db.jxl_is_lossless_cached("a", 200))
        db.save_jxl_lossless("a", 200, False)
        self.assertIs(db.jxl_is_lossless_cached("a", 200), False)

    def test_filter_keeps_only_lossless_jxl(self):
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        client = app.test_client()
        routes._asset_cache.clear()
        assets = [
            {"id": aid, "ownerId": "u", "type": "IMAGE", "originalFileName": name,
             "exifInfo": {"fileSizeInByte": 1000}}
            for aid, name in (("lossy", "a.jxl"), ("lossless", "b.jxl"), ("jpeg", "c.jpg"))
        ]
        verdicts = {"lossy": False, "lossless": True}
        with patch.object(routes, "get_env", return_value=ENV), patch.object(routes, "fetch_users"), \
                patch.object(routes, "owner_name_for", return_value=""), \
                patch("backend.immich_api.owner_name_for", return_value=""), \
                patch.object(routes, "search_metadata_all", return_value=assets), \
                patch.object(jxl_probe, "_classify", side_effect=lambda env, aid, size: (verdicts[aid], True)) as classify:
            body = client.get("/api/assets?media=image&jxl_lossless=1").get_json()
            self.assertEqual([a["name"] for a in body["assets"]], ["b.jxl"])
            self.assertEqual(classify.call_count, 2)
            routes._asset_cache.clear()
            client.get("/api/assets?media=image&jxl_lossless=1")
            self.assertEqual(classify.call_count, 2, "a second scan reads the SQLite cache")
            body = client.get("/api/assets?media=image").get_json()
            self.assertEqual(body["total"], 3)


LOSSLESS_LINE = "JPEG XL image, 64x48, (possibly) lossless, 8-bit RGB"


def tiny_response(*args, **kwargs):
    resp = MagicMock(status_code=206)
    resp.__enter__.return_value = resp
    resp.iter_content.return_value = iter([b"x" * 2 * 1024 ** 2])
    return resp


class RetryAndOrderTest(TempDbTest):
    def test_retry_with_one_megabyte_classifies(self):
        with patch.object(jxl_probe.requests, "get", side_effect=tiny_response) as get, \
                patch.object(jxl_probe.shutil, "which", return_value="/usr/bin/jxlinfo"), \
                patch.object(jxl_probe, "_jxlinfo", side_effect=["Unexpected EOF", LOSSLESS_LINE]):
            self.assertIs(jxl_probe.classify_jxl(ENV, "a", 5 * 1024 ** 2), True)
        self.assertEqual([c.kwargs["headers"]["Range"] for c in get.call_args_list],
                         ["bytes=0-65535", "bytes=0-1048575"])

    def test_unclassified_is_cached_as_excluded(self):
        summary = {"id": "a", "format": "JXL", "size": 5 * 1024 ** 2}
        with patch.object(jxl_probe.requests, "get", side_effect=tiny_response) as get, \
                patch.object(jxl_probe.shutil, "which", return_value="/usr/bin/jxlinfo"), \
                patch.object(jxl_probe, "_jxlinfo", return_value="Unexpected EOF"):
            for _ in range(2):
                self.assertEqual(jxl_probe.lossless_jxl_only(ENV, [summary]), ([], 0))
        self.assertEqual(get.call_count, 2, "both reads on the first load, none on the second")
        self.assertIsNone(db.jxl_is_lossless_cached("a", summary["size"] + 1))

    def test_failed_download_is_not_cached(self):
        summary = {"id": "a", "format": "JXL", "size": 5 * 1024 ** 2}
        with patch.object(jxl_probe.requests, "get", side_effect=jxl_probe.requests.ConnectionError) as get, \
                patch.object(jxl_probe.shutil, "which", return_value="/usr/bin/jxlinfo"):
            for _ in range(2):
                self.assertEqual(jxl_probe.lossless_jxl_only(ENV, [summary]), ([], 1))
        self.assertEqual(get.call_count, 2, "a network error is retried on the next load")
        self.assertIsNone(db.jxl_is_lossless_cached("a", summary["size"]))

    def test_probe_runs_after_size_megapixel_and_format_filters(self):
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        client = app.test_client()
        assets = [
            {"id": aid, "ownerId": "u", "type": "IMAGE", "originalFileName": "x.jxl",
             "exifInfo": {"fileSizeInByte": mb * 1024 ** 2}}
            for aid, mb in (("in-range", 1), ("too-big", 20))
        ]
        with patch.object(routes, "get_env", return_value=ENV), patch.object(routes, "fetch_users"), \
                patch("backend.immich_api.owner_name_for", return_value=""), \
                patch.object(routes, "search_metadata_all", return_value=assets), \
                patch.object(jxl_probe.shutil, "which", return_value="/usr/bin/jxlinfo"), \
                patch.object(jxl_probe, "_jxlinfo", return_value=LOSSLESS_LINE), \
                patch.object(jxl_probe.requests, "get", side_effect=tiny_response) as get:
            for query in ("format=TIFF", "min_mp=1"):
                routes._asset_cache.clear()
                body = client.get("/api/assets?media=image&jxl_lossless=1&" + query).get_json()
                self.assertEqual(body["assets"], [])
            get.assert_not_called()
            body = client.get("/api/assets?media=image&jxl_lossless=1&max_mb=10").get_json()
        self.assertEqual([a["id"] for a in body["assets"]], ["in-range"])
        self.assertEqual([c.args[0] for c in get.call_args_list],
                         ["https://immich.test/api/assets/in-range/original"])


class CacheScaleTest(TempDbTest):
    def summaries(self, count):
        return [{"id": f"a{i}", "format": "JXL", "size": 100} for i in range(count)]

    def test_cached_assets_are_read_in_one_query(self):
        for item in self.summaries(5):
            db.save_jxl_lossless(item["id"], 100, item["id"] != "a0")
        statements = []
        real_connect = db.sqlite3.connect

        def traced(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn
        with patch.object(db.sqlite3, "connect", side_effect=traced), \
                patch.object(jxl_probe, "_classify") as classify:
            kept, unchecked = jxl_probe.lossless_jxl_only(ENV, self.summaries(5))
        classify.assert_not_called()
        self.assertEqual(([s["id"] for s in kept], unchecked), (["a1", "a2", "a3", "a4"], 0))
        self.assertEqual(len([q for q in statements if q.lstrip().upper().startswith("SELECT")]), 1)

    def test_concurrent_requests_probe_an_asset_once(self):
        release = threading.Event()
        calls = []

        def slow(env, aid, size):
            calls.append(aid)
            release.wait(5)
            return True, True
        results = []
        with patch.object(jxl_probe, "_classify", side_effect=slow):
            workers = [threading.Thread(target=lambda: results.append(
                jxl_probe.lossless_jxl_only(ENV, self.summaries(1)))) for _ in range(2)]
            for worker in workers:
                worker.start()
            time.sleep(0.3)
            release.set()
            for worker in workers:
                worker.join(5)
        self.assertEqual(calls, ["a0"])
        self.assertEqual([len(kept) for kept, _ in results], [1, 1])

    def test_unknown_is_retried_after_seven_days(self):
        db.save_jxl_lossless("a", 100, None)
        db.save_jxl_lossless("b", 100, True)
        self.assertEqual(db.jxl_lossless_cached_many([("a", 100), ("b", 100)]), {"a": False, "b": True})
        later = time.time() + 8 * 86400
        with patch.object(db.time, "time", return_value=later):
            self.assertEqual(db.jxl_lossless_cached_many([("a", 100), ("b", 100)]), {"b": True})

    def test_old_table_gains_the_timestamp_column(self):
        import sqlite3
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("DROP TABLE jxl_lossless")
        conn.execute("CREATE TABLE jxl_lossless (asset_id TEXT PRIMARY KEY, size INTEGER, lossless INTEGER)")
        conn.execute("INSERT INTO jxl_lossless VALUES ('old', 100, NULL)")
        conn.commit()
        conn.close()
        db.db_init()
        # An unknown saved before the column existed is retried at once.
        self.assertEqual(db.jxl_lossless_cached_many([("old", 100)]), {})
        db.save_jxl_lossless("old", 100, None)
        self.assertEqual(db.jxl_lossless_cached_many([("old", 100)]), {"old": False})

    def test_unchecked_count_is_in_the_response(self):
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        client = app.test_client()
        routes._asset_cache.clear()
        assets = [{"id": f"a{i}", "ownerId": "u", "type": "IMAGE", "originalFileName": "x.jxl",
                   "exifInfo": {"fileSizeInByte": 1000}} for i in range(3)]
        with patch.object(routes, "get_env", return_value=ENV), patch.object(routes, "fetch_users"), \
                patch("backend.immich_api.owner_name_for", return_value=""), \
                patch.object(routes, "search_metadata_all", return_value=assets), \
                patch.object(jxl_probe.shutil, "which", return_value=None):
            body = client.get("/api/assets?media=image&jxl_lossless=1").get_json()
            self.assertEqual((body["total"], body["jxl_unchecked"]), (0, 3))
            self.assertNotIn("jxl_unchecked", client.get("/api/assets?media=image").get_json())


if __name__ == "__main__":
    unittest.main()
