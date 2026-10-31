"""Video playback proxy regressions with a controlled upstream response."""

import unittest
from unittest.mock import Mock, patch

import requests
from flask import Flask

from backend import routes

ASSET = "30000000-0000-4000-8000-000000000001"
ENV = {"url": "https://immich.test", "api_keys": ["reader"], "api_key": "reader"}


def upstream(status=200, headers=None, chunks=(b"video",)):
    result = Mock(status_code=status, headers=headers or {})
    result.iter_content.return_value = iter(chunks)
    return result


class PlaybackProxyTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        env = patch.object(routes, "get_env", return_value=ENV)
        env.start()
        self.addCleanup(env.stop)

    def get(self, fake, **kwargs):
        with patch.object(routes.requests, "get", return_value=fake) as get:
            result = self.client.get(f"/api/playback/{ASSET}", **kwargs)
            body = result.get_data()
            result.close()
        return result, body, get

    def test_range_is_forwarded_and_partial_content_passed_back(self):
        fake = upstream(206, {
            "Content-Type": "video/quicktime", "Content-Length": "5",
            "Content-Range": "bytes 10-14/100", "Accept-Ranges": "bytes",
        })
        result, body, get = self.get(fake, headers={"Range": "bytes=10-14"})
        self.assertEqual(result.status_code, 206)
        self.assertEqual(body, b"video")
        self.assertEqual(result.headers["Content-Range"], "bytes 10-14/100")
        self.assertEqual(result.headers["Accept-Ranges"], "bytes")
        self.assertEqual(result.headers["Content-Type"], "video/quicktime")
        self.assertEqual(get.call_args.args[0],
                         f"https://immich.test/api/assets/{ASSET}/video/playback")
        self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=10-14")

    def test_request_without_range_sends_none_upstream(self):
        _, _, get = self.get(upstream(headers={"Content-Type": "video/mp4"}))
        self.assertNotIn("Range", get.call_args.kwargs["headers"])

    def test_upstream_connection_is_released_when_the_client_goes_away(self):
        fake = upstream()
        self.get(fake)
        fake.close.assert_called()

    def test_upstream_error_status_is_reported(self):
        fake = upstream(404)
        result, _, _ = self.get(fake)
        self.assertEqual(result.status_code, 404)
        fake.close.assert_called_once()

    def test_unreachable_immich_is_a_bad_gateway(self):
        with patch.object(routes.requests, "get", side_effect=requests.ConnectionError("down")):
            result = self.client.get(f"/api/playback/{ASSET}")
        self.assertEqual(result.status_code, 502)

    def test_non_uuid_asset_id_is_rejected_before_any_upstream_call(self):
        with patch.object(routes.requests, "get") as get:
            result = self.client.get("/api/playback/not-a-uuid")
        self.assertEqual(result.status_code, 400)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
