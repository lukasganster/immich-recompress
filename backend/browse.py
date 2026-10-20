"""Metadata discovery and complete, owner-scoped filtered media collection."""

import time

import requests

from backend.config import HTTP_TIMEOUT, USER_CACHE_TTL
from backend.immich_api import (
    UUID_RE, StructuredSearchError, env_for_key, fetch_asset, fetch_key_owners,
    immich_headers, motion_summary, search_metadata_filtered, video_summary,
)

from backend.state import _discovery_cache, _discovery_lock


def metadata_filters(args):
    """Validate metadata parameters without silently discarding a constraint."""
    filters = {field: (args.get(field) or "").strip()
               for field in ("album_id", "city", "state", "country")}
    people = sorted(set(args.getlist("person_id")))
    for value in [filters["album_id"], *people]:
        if value and not UUID_RE.fullmatch(value):
            raise ValueError("Album and person filters must use valid Immich UUIDs.")
    if any(not value for value in people):
        raise ValueError("A person filter cannot be empty.")
    filters["person_ids"] = people
    return filters


def build_filter(media, metadata, capture_dates=None):
    """Compose Immich structured conditions, with date bounds on the still."""
    result = {"type": {"eq": "VIDEO" if media == "video" else "IMAGE"}}
    if media == "motionphoto":
        result["isMotion"] = {"eq": True}
    if metadata["album_id"]:
        result["albumIds"] = {"any": [metadata["album_id"]]}
    if metadata["person_ids"]:
        result["personIds"] = {"any": metadata["person_ids"]}
    for field in ("city", "state", "country"):
        if metadata[field]:
            result[field] = {"eq": metadata[field]}
    if capture_dates:
        result["takenAt"] = {}
        for bound, field in (("start", "gte"), ("end", "lt")):
            if capture_dates[bound]:
                result["takenAt"][field] = capture_dates[bound]
    return result


def _get(env, path, params=None):
    try:
        response = requests.get(env["url"] + path, headers=immich_headers(env),
                                params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise StructuredSearchError(
            "Could not load Immich browse metadata. Check the selected API keys and their permissions.") from exc


def discover(env, key_indices, kind, search=""):
    """Merge album summaries or paginated people, preserving key provenance."""
    cache_key = (env["url"], tuple(env["api_keys"]), tuple(key_indices), kind, search)
    with _discovery_lock:
        cached = _discovery_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < USER_CACHE_TTL:
            return cached[1]
    merged = {}
    for idx in key_indices:
        key_env = env_for_key(env, idx)
        page = 1
        seen_pages = set()
        while True:
            params = {"page": page, "size": 1000}
            if search:
                params["name"] = search
            data = _get(key_env, "/api/albums" if kind == "albums" else "/api/people",
                        None if kind == "albums" else params)
            items = data if kind == "albums" else data.get("people") if isinstance(data, dict) else None
            if not isinstance(items, list) or any(
                    not isinstance(item, dict) or not UUID_RE.fullmatch(str(item.get("id", "")))
                    for item in items):
                raise StructuredSearchError("Immich returned invalid browse metadata.")
            signature = tuple(item["id"] for item in items)
            if signature and signature in seen_pages:
                raise StructuredSearchError("Immich repeated a people page. Try a narrower search.")
            seen_pages.add(signature)
            for item in items:
                entry = merged.setdefault(item["id"], {
                    "id": item["id"], "name": item.get("albumName" if kind == "albums" else "name") or "Unnamed",
                    "owner_id": item.get("ownerId") or (item.get("owner") or {}).get("id") or "",
                    "key_indices": [],
                })
                if idx not in entry["key_indices"]:
                    entry["key_indices"].append(idx)
            if kind == "albums" or data.get("hasNextPage") is False:
                break
            if not isinstance(data.get("hasNextPage"), bool) or not items or page >= 100:
                raise StructuredSearchError("Immich people discovery did not complete. Narrow the search.")
            page += 1
    result = sorted(merged.values(), key=lambda item: (item["name"].casefold(), item["id"]))
    with _discovery_lock:
        now = time.monotonic()
        for key in list(_discovery_cache):
            if now - _discovery_cache[key][0] >= USER_CACHE_TTL:
                del _discovery_cache[key]
        # Bound caches even when many distinct person-name queries are used.
        if len(_discovery_cache) >= 128:
            _discovery_cache.pop(next(iter(_discovery_cache)))
        _discovery_cache[cache_key] = (now, result)
    return result


def collect_filtered(env, key_indices, media, metadata, capture_dates,
                     min_bytes, user_filter, search_filter, codec_filter):
    """Return complete summaries and owner-key assignments, ready to publish."""
    owners = {item["key_idx"]: item["id"] for item in fetch_key_owners(env)}
    if any(not owners.get(idx) for idx in key_indices):
        raise StructuredSearchError("Could not identify every selected library owner. Check the API keys.")
    owner_keys = {}
    for idx in key_indices:
        owner_keys.setdefault(owners[idx], idx)
    scan_keys = list(key_indices)
    if metadata["album_id"]:
        album = next((item for item in discover(env, key_indices, "albums")
                      if item["id"] == metadata["album_id"]), None)
        if not album:
            raise ValueError("The selected album is unavailable. Clear it or choose another album.")
        scan_keys = album["key_indices"]
    people = {}
    if metadata["person_ids"]:
        people = {item["id"]: item["key_indices"] for item in discover(env, key_indices, "people")}
        if any(pid not in people for pid in metadata["person_ids"]):
            raise ValueError("A selected person is unavailable. Clear it or choose another person.")
    collected = {}
    for idx in scan_keys:
        scoped = dict(metadata)
        if metadata["person_ids"]:
            scoped["person_ids"] = [pid for pid in metadata["person_ids"] if idx in people[pid]]
            if not scoped["person_ids"]:
                continue
        for asset in search_metadata_filtered(env_for_key(env, idx), build_filter(media, scoped, capture_dates)):
            owner_id = asset.get("ownerId")
            if not owner_id:
                raise StructuredSearchError("Immich omitted asset ownership from a filtered result.")
            if owner_id in owner_keys:
                collected[asset["id"]] = (asset, owner_keys[owner_id])
    summaries, assignments = [], {}
    for aid, (asset, idx) in collected.items():
        if user_filter and asset["ownerId"] != user_filter:
            continue
        key_env = env_for_key(env, idx)
        if media == "motionphoto":
            vid = asset.get("livePhotoVideoId")
            clip = fetch_asset(key_env, vid) if vid else None
            size = ((clip or {}).get("exifInfo") or {}).get("fileSizeInByte") or (clip or {}).get("fileSizeInByte")
            if not clip or size is None:
                raise StructuredSearchError("Could not read a matching Live Photo's motion clip through its owner's key.")
            try:
                summary = motion_summary(key_env, asset, clip, int(size))
            except (ValueError, TypeError) as exc:
                raise StructuredSearchError("Immich returned an invalid motion-clip size.") from exc
        else:
            summary = video_summary(key_env, asset, media)
        if summary["size"] < min_bytes or (search_filter and search_filter not in summary["name"].lower()):
            continue
        if media == "video" and codec_filter:
            codec = summary["codec"].lower()
            if codec_filter in ("other", "__other__"):
                if codec in ("h264", "hevc", "av1"):
                    continue
            elif codec != codec_filter:
                continue
        summaries.append(summary)
        assignments[aid] = idx
    return summaries, assignments
