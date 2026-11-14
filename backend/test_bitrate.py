"""Bitrate derived from size and duration, and sorting the asset list by it."""

import unittest
from unittest.mock import patch

from flask import Flask

from backend import immich_api
from backend.config import average_bitrate

ENV = {"url": "https://immich.test", "api_keys": ["k"], "api_key": "k"}


def video(aid, size, duration_ms, exif_bitrate=None):
    info = {"fileSizeInByte": size, "codec": "h264"}
    if exif_bitrate is not None:
        info["bitrate"] = exif_bitrate
    return {"id": aid, "type": "VIDEO", "originalFileName": f"{aid}.mp4",
            "duration": duration_ms, "exifInfo": info}


class AverageBitrateTest(unittest.TestCase):
    def test_size_over_duration_in_bits(self):
        self.assertEqual(average_bitrate(10_000_000, 4), 20_000_000)

    def test_unknown_when_inputs_missing_or_zero(self):
        for size, duration in [(0, 10), (100, 0), (None, 10), (100, None), ("x", 1), (100, -1)]:
            self.assertIsNone(average_bitrate(size, duration))


class VideoSummaryBitrateTest(unittest.TestCase):
    def summary(self, asset, media="video"):
        with patch.object(immich_api, "owner_name_for", return_value=""):
            return immich_api.video_summary(ENV, asset, media=media)

    def test_video_bitrate_is_derived(self):
        self.assertEqual(self.summary(video("a", 10_000_000, 4000))["bitrate"], 20_000_000)

    def test_reported_bitrate_wins(self):
        self.assertEqual(self.summary(video("a", 10_000_000, 4000, exif_bitrate=123))["bitrate"], 123)

    def test_missing_duration_leaves_bitrate_unknown(self):
        self.assertIsNone(self.summary(video("a", 10_000_000, 0))["bitrate"])

    def test_images_get_no_bitrate(self):
        self.assertIsNone(self.summary(video("a", 10_000_000, 4000), media="image")["bitrate"])


class BitrateSortTest(unittest.TestCase):
    def order(self, direction):
        rows = [{"id": "low", "bitrate": 1}, {"id": "none", "bitrate": None},
                {"id": "high", "bitrate": 9}, {"id": "mid", "bitrate": 5}]
        for row in rows:
            row.update(size=0, potential=0)
        with Flask(__name__).app_context():
            resp = immich_api.paginate_summaries(rows, "bitrate", direction, 1, 10)
        return [a["id"] for a in resp.get_json()["assets"]]

    def test_descending_puts_unknown_last(self):
        self.assertEqual(self.order("desc"), ["high", "mid", "low", "none"])

    def test_ascending_puts_unknown_last(self):
        self.assertEqual(self.order("asc"), ["low", "mid", "high", "none"])


if __name__ == "__main__":
    unittest.main()
