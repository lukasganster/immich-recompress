"""Profile picture proxy for API key owners in the library selector."""

import unittest
from unittest.mock import Mock, patch

import requests
from flask import Flask

from backend import routes

ENV = {"url": "https://immich.test", "api_keys": ["first", "second"], "api_key": "first"}
OWNERS = [
    {"key_idx": 0, "id": "user-a", "name": "A", "email": "", "has_avatar": True,
     "avatar_version": "2026-01-01T00:00:00Z"},
    {"key_idx": 1, "id": "user-b", "name": "B", "email": "", "has_avatar": False,
     "avatar_version": ""},
]


class KeyAvatarTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        for target, value in (("get_env", ENV), ("fetch_key_owners", OWNERS)):
            patcher = patch.object(routes, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_picture_is_fetched_with_the_owners_own_key(self):
        fake = Mock(status_code=200, headers={"Content-Type": "image/png"}, content=b"png")
        with patch.object(routes.requests, "get", return_value=fake) as get:
            result = self.client.get("/api/keys/0/avatar?v=x")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_data(), b"png")
        self.assertEqual(result.headers["Content-Type"], "image/png")
        self.assertEqual(get.call_args.args[0], "https://immich.test/api/users/user-a/profile-image")
        self.assertEqual(get.call_args.kwargs["headers"]["x-api-key"], "first")

    def test_owner_without_picture_is_not_fetched(self):
        with patch.object(routes.requests, "get") as get:
            result = self.client.get("/api/keys/1/avatar")
        self.assertEqual(result.status_code, 404)
        get.assert_not_called()

    def test_unknown_key_is_rejected(self):
        with patch.object(routes.requests, "get") as get:
            result = self.client.get("/api/keys/5/avatar")
        self.assertEqual(result.status_code, 404)
        get.assert_not_called()

    def test_upstream_failure_reports_bad_gateway(self):
        with patch.object(routes.requests, "get", side_effect=requests.ConnectionError):
            result = self.client.get("/api/keys/0/avatar")
        self.assertEqual(result.status_code, 502)


if __name__ == "__main__":
    unittest.main()
