import unittest
from unittest.mock import patch

from backend.config import parse_duration
from backend.immich_api import search_metadata_filtered, supports_structured_search
from backend.routes import _legacy_capture_filter, _normalise_capture_dates, _structured_capture_filter


class ParseDurationTest(unittest.TestCase):
    def test_immich_milliseconds(self):
        self.assertAlmostEqual(parse_duration(4086), 4.086)
        self.assertAlmostEqual(parse_duration("109886"), 109.886)

    def test_immich_clock_duration(self):
        self.assertAlmostEqual(parse_duration("0:00:30.31200"), 30.312)
        self.assertAlmostEqual(parse_duration("1:02:03.5"), 3723.5)


class CaptureDateFilterTest(unittest.TestCase):
    def test_dates_are_converted_to_a_half_open_utc_interval_across_dst(self):
        capture_dates, error = _normalise_capture_dates({
            "date_from": "2024-03-31", "date_to": "2024-03-31",
            "timezone": "Europe/Vienna",
        })

        self.assertIsNone(error)
        self.assertEqual(capture_dates["start"], "2024-03-30T23:00:00Z")
        self.assertEqual(capture_dates["end"], "2024-03-31T22:00:00Z")
        self.assertEqual(capture_dates["legacy_end"], "2024-03-31T21:59:59.999999Z")
        self.assertEqual(_structured_capture_filter("VIDEO", capture_dates), {
            "type": {"eq": "VIDEO"},
            "takenAt": {
                "gte": "2024-03-30T23:00:00Z",
                "lt": "2024-03-31T22:00:00Z",
            },
        })

    def test_reversed_dates_are_rejected(self):
        capture_dates, error = _normalise_capture_dates({
            "date_from": "2024-08-02", "date_to": "2024-08-01",
            "timezone": "UTC",
        })

        self.assertIsNone(capture_dates)
        self.assertEqual(error, "The capture-date start must not be after the end date.")

    def test_structured_search_uses_cursor_pagination_without_legacy_fields(self):
        class Response:
            status_code = 200

            def __init__(self, items, cursor=None):
                self.items = items
                self.cursor = cursor

            def json(self):
                return {"assets": {"items": self.items, "nextCursor": self.cursor}}

        responses = [Response([{"id": "first"}], "cursor-2"), Response([{"id": "second"}])]
        with patch("backend.immich_api.requests.post", side_effect=responses) as post:
            items = search_metadata_filtered(
                {"url": "https://immich.test", "api_key": "key"},
                {"type": {"eq": "VIDEO"}, "takenAt": {"gte": "2024-01-01T00:00:00Z"}},
            )

        self.assertEqual([item["id"] for item in items], ["first", "second"])
        first_body = post.call_args_list[0].kwargs["json"]
        second_body = post.call_args_list[1].kwargs["json"]
        self.assertNotIn("page", first_body)
        self.assertNotIn("type", first_body)
        self.assertEqual(second_body["cursor"], "cursor-2")

    def test_legacy_search_uses_an_inclusive_upper_bound(self):
        capture_dates, error = _normalise_capture_dates({
            "date_from": "2024-03-31", "date_to": "2024-03-31",
            "timezone": "Europe/Vienna",
        })

        self.assertIsNone(error)
        self.assertEqual(_legacy_capture_filter(capture_dates), {
            "takenAfter": "2024-03-30T23:00:00Z",
            "takenBefore": "2024-03-31T21:59:59.999999Z",
        })

    def test_structured_search_requires_immich_3_2_or_newer(self):
        with patch("backend.immich_api.detect_immich_version", return_value="3.1.0"):
            self.assertFalse(supports_structured_search({}))
        with patch("backend.immich_api.detect_immich_version", return_value="3.2.0"):
            self.assertTrue(supports_structured_search({}))

if __name__ == "__main__":
    unittest.main()
