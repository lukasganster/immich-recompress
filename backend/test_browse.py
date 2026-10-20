"""Browse integration regressions with controlled upstream responses."""

import unittest
from unittest.mock import Mock, patch

import requests
from flask import Flask
from werkzeug.datastructures import MultiDict

from backend import browse, immich_api, routes
from backend.immich_api import StructuredSearchError
from backend.state import _asset_key_map

ALBUM = "10000000-0000-4000-8000-000000000001"
PERSON_A = "20000000-0000-4000-8000-000000000001"
PERSON_B = "20000000-0000-4000-8000-000000000002"
ASSET_A = "30000000-0000-4000-8000-000000000001"
ASSET_B = "30000000-0000-4000-8000-000000000002"
ENV = {"url": "https://immich.test", "api_keys": ["reader", "owner"], "api_key": "reader"}
OWNERS = [{"key_idx": 0, "id": "user-a"}, {"key_idx": 1, "id": "user-b"}]


def response(data, status=200):
    result = Mock(status_code=status)
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError()
    return result


def asset(aid=ASSET_A, owner="user-b", size=500):
    return {"id": aid, "ownerId": owner, "type": "VIDEO", "originalFileName": "holiday.mp4",
            "exifInfo": {"fileSizeInByte": size, "codec": "h264"}}


def filters(**values):
    return {"album_id": "", "person_ids": [], "city": "", "state": "", "country": "", **values}


class SearchPaginationTest(unittest.TestCase):
    def test_cursor_is_followed_without_legacy_parameters(self):
        with patch.object(immich_api.requests, "post", side_effect=[
            response({"assets": {"items": [asset()], "nextCursor": "next"}}),
            response({"assets": {"items": [asset(ASSET_B)], "nextCursor": None}}),
        ]) as post:
            result = immich_api.search_metadata_filtered(ENV, {"albumIds": {"any": [ALBUM]}})
        self.assertEqual(len(result), 2)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["cursor"], "next")
        self.assertNotIn("page", post.call_args.kwargs["json"])
        self.assertNotIn("albumIds", post.call_args.kwargs["json"])

    def test_partial_cursor_results_fail(self):
        bad_pages = [
            response({}), response({"assets": {"items": [], "nextCursor": "next"}}),
            response({"assets": {"items": []}}), response({}, 403),
            response({"assets": {"items": "invalid", "nextCursor": None}}),
            response({"assets": {"items": [{}], "nextCursor": None}}),
            requests.ConnectionError(),
        ]
        for bad in bad_pages:
            with self.subTest(bad=bad), patch.object(immich_api.requests, "post", side_effect=[
                response({"assets": {"items": [asset()], "nextCursor": "next"}}), bad,
            ]), self.assertRaises(StructuredSearchError):
                immich_api.search_metadata_filtered(ENV, {"type": {"eq": "VIDEO"}})

    def test_cursor_limit_is_an_error(self):
        with patch.object(immich_api.requests, "post", return_value=response({
            "assets": {"items": [asset()], "nextCursor": "next"},
        })), self.assertRaisesRegex(StructuredSearchError, "scan limit"):
            immich_api.search_metadata_filtered(ENV, {}, max_pages=1)

    def test_legacy_follows_token_and_rejects_incomplete_results(self):
        with patch.object(immich_api.requests, "post", side_effect=[
            response({"assets": {"items": [asset()], "nextPage": "3"}}),
            response({"assets": {"items": [], "nextPage": None}}),
        ]) as post:
            self.assertEqual(len(immich_api.search_metadata_all(ENV, {})), 1)
        self.assertEqual(post.call_args.kwargs["json"]["page"], 3)
        for block in ({"items": []}, {"items": [asset()], "nextPage": "1"}):
            with self.subTest(block=block), patch.object(immich_api.requests, "post", return_value=response({"assets": block})), self.assertRaises(StructuredSearchError):
                immich_api.search_metadata_all(ENV, {})
        with patch.object(immich_api.requests, "post", return_value=response({"assets": {
            "items": [asset()], "nextPage": "2",
        }})), self.assertRaisesRegex(StructuredSearchError, "scan limit"):
            immich_api.search_metadata_all(ENV, {}, max_pages=1)


class MetadataBrowseTest(unittest.TestCase):
    def setUp(self):
        browse._discovery_cache.clear()
        routes._asset_cache.clear()
        self.map_before = dict(_asset_key_map)
        self.addCleanup(lambda: (_asset_key_map.clear(), _asset_key_map.update(self.map_before)))
        owner_patch = patch.object(browse, "fetch_key_owners", return_value=OWNERS)
        owner_patch.start()
        self.addCleanup(owner_patch.stop)
        name_patch = patch.object(immich_api, "owner_name_for", return_value="Owner")
        name_patch.start()
        self.addCleanup(name_patch.stop)

    def collect(self, metadata=None, media="video", keys=None, **kwargs):
        return browse.collect_filtered(ENV, [0, 1] if keys is None else keys, media,
                                       metadata or filters(), kwargs.get("dates"), kwargs.get("min_bytes", 0),
                                       kwargs.get("user", ""), kwargs.get("search", ""), kwargs.get("codec", ""))

    def test_builder_composes_all_metadata_and_dates(self):
        metadata = browse.metadata_filters(MultiDict([
            ("album_id", ALBUM), ("person_id", PERSON_B), ("person_id", PERSON_A),
            ("person_id", PERSON_B), ("city", " Vienna "), ("state", "Vienna"), ("country", "Austria"),
        ]))
        result = browse.build_filter("motionphoto", metadata, {"start": "start", "end": "end"})
        self.assertEqual(result, {"type": {"eq": "IMAGE"}, "isMotion": {"eq": True},
                                  "albumIds": {"any": [ALBUM]}, "personIds": {"any": [PERSON_A, PERSON_B]},
                                  "city": {"eq": "Vienna"}, "state": {"eq": "Vienna"},
                                  "country": {"eq": "Austria"}, "takenAt": {"gte": "start", "lt": "end"}})
        for query in ({"album_id": "bad"}, {"person_id": ""}, {"person_id": "bad"}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                browse.metadata_filters(MultiDict(query))

    def test_discovery_merges_shared_albums_and_caches_by_keys_and_credentials(self):
        with patch.object(browse, "_get", return_value=[{"id": ALBUM, "albumName": "Trip", "ownerId": "user-b"}]) as get:
            albums = browse.discover(ENV, [0, 1], "albums")
            self.assertEqual(albums[0]["key_indices"], [0, 1])
            self.assertEqual(albums[0]["owner_id"], "user-b")
            browse.discover(ENV, [0, 1], "albums")
            self.assertEqual(get.call_count, 2)
            browse.discover(ENV, [1], "albums")
            browse.discover({**ENV, "api_keys": ["changed"]}, [0], "albums")
            self.assertEqual(get.call_count, 4)

    def test_people_discovery_preserves_same_name_ids_and_pages(self):
        with patch.object(browse, "_get", side_effect=[
            {"people": [{"id": PERSON_A, "name": "Alex"}], "hasNextPage": True},
            {"people": [{"id": PERSON_B, "name": "Alex"}], "hasNextPage": False},
        ]) as get:
            people = browse.discover(ENV, [1], "people", "Alex")
        self.assertEqual({p["id"] for p in people}, {PERSON_A, PERSON_B})
        self.assertEqual(get.call_args.args[2], {"page": 2, "size": 1000, "name": "Alex"})

    def test_failed_discovery_is_not_cached(self):
        with patch.object(browse, "_get", side_effect=[[{"id": ALBUM}], StructuredSearchError("failed")]), self.assertRaises(StructuredSearchError):
            browse.discover(ENV, [0, 1], "albums")
        self.assertFalse(browse._discovery_cache)

    def test_shared_results_are_unique_and_use_owner_key(self):
        with patch.object(browse, "discover", return_value=[{"id": ALBUM, "key_indices": [0, 1]}]), patch.object(
            browse, "search_metadata_filtered", return_value=[asset(), asset(ASSET_B, "foreign")]
        ) as search:
            summaries, assignments = self.collect(filters(album_id=ALBUM))
        self.assertEqual(len(summaries), 1)
        self.assertEqual(assignments, {ASSET_A: 1})
        self.assertEqual(search.call_count, 2)

    def test_unselected_owner_is_excluded_even_if_shared_asset_is_visible(self):
        with patch.object(browse, "search_metadata_filtered", return_value=[asset()]):
            self.assertEqual(self.collect(filters(city="Vienna"), keys=[0]), ([], {}))

    def test_people_ids_are_scoped_by_library_without_name_substitution(self):
        with patch.object(browse, "discover", return_value=[{"id": PERSON_A, "key_indices": [1]}]), patch.object(
            browse, "search_metadata_filtered", return_value=[asset()]
        ) as search:
            self.collect(filters(person_ids=[PERSON_A]))
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.args[0]["api_key"], "owner")
        self.assertEqual(search.call_args.args[1]["personIds"], {"any": [PERSON_A]})

    def test_stale_album_and_person_ids_fail_without_scanning(self):
        for metadata in (filters(album_id=ALBUM), filters(person_ids=[PERSON_A])):
            with self.subTest(metadata=metadata), patch.object(browse, "discover", return_value=[]), patch.object(
                browse, "search_metadata_filtered"
            ) as search, self.assertRaises(ValueError):
                self.collect(metadata)
            search.assert_not_called()

    def test_only_keys_with_album_access_participate(self):
        with patch.object(browse, "discover", return_value=[{"id": ALBUM, "key_indices": [0]}]), patch.object(
            browse, "search_metadata_filtered", return_value=[asset()]
        ) as search:
            summaries, assignments = self.collect(filters(album_id=ALBUM))
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.args[0]["api_key"], "reader")
        self.assertEqual(assignments, {ASSET_A: 1})
        self.assertEqual(len(summaries), 1)

    def test_missing_owner_identity_and_partial_key_failure_fail_the_scan(self):
        with patch.object(browse, "fetch_key_owners", return_value=[OWNERS[0], {"key_idx": 1, "id": ""}]), self.assertRaises(StructuredSearchError):
            self.collect(filters(city="Vienna"))
        before = dict(_asset_key_map)
        with patch.object(browse, "search_metadata_filtered", side_effect=[[asset()], StructuredSearchError("failed key")]), self.assertRaises(StructuredSearchError):
            self.collect(filters(city="Vienna"))
        self.assertEqual(dict(_asset_key_map), before)

    def test_owner_and_local_constraints_compose(self):
        with patch.object(browse, "search_metadata_filtered", return_value=[asset()]):
            self.assertEqual(len(self.collect(filters(city="Vienna"), min_bytes=400, search="holiday", codec="h264", user="user-b")[0]), 1)
            for kwargs in ({"min_bytes": 600}, {"search": "other"}, {"codec": "hevc"}, {"codec": "other"}, {"user": "user-a"}):
                with self.subTest(kwargs=kwargs):
                    self.assertEqual(self.collect(filters(city="Vienna"), **kwargs), ([], {}))

    def test_motion_filter_applies_to_still_and_clip_uses_owner_key(self):
        still = {**asset(), "type": "IMAGE", "livePhotoVideoId": ASSET_B}
        with patch.object(browse, "search_metadata_filtered", return_value=[still]) as search, patch.object(
            browse, "fetch_asset", return_value=asset(ASSET_B, size=600)
        ) as fetch:
            summaries, assignments = self.collect(filters(city="Vienna"), media="motionphoto", min_bytes=550)
        self.assertEqual(summaries[0]["size"], 600)
        self.assertEqual(assignments, {ASSET_A: 1})
        self.assertEqual(search.call_args.args[1]["isMotion"], {"eq": True})
        self.assertEqual(fetch.call_args.args[0]["api_key"], "owner")
        with patch.object(browse, "search_metadata_filtered", return_value=[still]), patch.object(
            browse, "fetch_asset", return_value=None
        ), self.assertRaises(StructuredSearchError):
            self.collect(filters(city="Vienna"), media="motionphoto")

    def test_owner_verification_does_not_accept_shared_readability(self):
        with patch.object(immich_api, "fetch_key_owners", return_value=OWNERS), patch.object(
            immich_api, "fetch_asset", return_value=asset()
        ):
            self.assertEqual(immich_api.env_for_asset_verified(ENV, ASSET_A)["api_key"], "owner")
            with self.assertRaises(StructuredSearchError):
                immich_api.env_for_asset_verified({**ENV, "api_keys": ["reader"]}, ASSET_A)


class BrowseRoutesTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        routes._asset_cache.clear()
        for name, value in (("get_env", ENV), ("fetch_users", []), ("supports_structured_search", True)):
            mock = patch.object(routes, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_invalid_filters_or_explicit_keys_are_rejected(self):
        for query in ("album_id=no", "person_id=no", "keys=99", "keys=", "keys=bad", "keys=0,bad"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/api/assets?" + query).status_code, 400)

    def test_new_filters_require_verified_structured_search(self):
        with patch.object(routes, "supports_structured_search", return_value=False), patch.object(routes, "collect_filtered") as collect:
            self.assertEqual(self.client.get("/api/assets?city=Vienna").status_code, 400)
        collect.assert_not_called()

    def test_complete_filtered_totals_are_cached_with_all_filters(self):
        summary = {"id": ASSET_A, "name": "holiday", "size": 500, "potential": 100}
        with patch.object(routes, "collect_filtered", return_value=([summary], {ASSET_A: 1})) as collect:
            first = self.client.get("/api/assets?city=Vienna&min_mb=0").get_json()
            self.client.get("/api/assets?city=Vienna&min_mb=0&page=2")
            self.assertEqual(collect.call_count, 1)
            self.client.get("/api/assets?city=Vienna&country=Austria&min_mb=0")
            self.assertEqual(collect.call_count, 2)
        self.assertEqual((first["total"], first["total_size"], first["total_potential"]), (1, 500, 100))

    def test_failed_scan_is_not_cached_or_published_as_success(self):
        for error, status in ((StructuredSearchError("scan failed"), 502), (ValueError("stale filter"), 400)):
            with self.subTest(error=error), patch.object(routes, "collect_filtered", side_effect=error):
                result = self.client.get("/api/assets?city=Vienna")
                self.assertEqual(result.status_code, status)
                self.assertNotIn("assets", result.get_json())
                self.assertFalse(routes._asset_cache)


if __name__ == "__main__":
    unittest.main()
