"""Immich HTTP client: env/key resolution, user cache, asset CRUD and the
parsing/summary helpers that turn Immich responses into UI rows.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from flask import jsonify

from backend.config import (
    FRONTEND_PER_PAGE, HTTP_TIMEOUT, IMMICH_PER_PAGE, JPEG_PHOTO_EXTS,
    RAW_PHOTO_EXTS, USER_CACHE_TTL, human_duration, human_size, parse_duration,
    utcnow_iso,
)
from backend.state import (
    _asset_key_lock, _asset_key_map, _jobs, _state_lock, is_cancelled,
)

def env_for_key(env, key_idx):
    """Return a copy of env with api_key set to the key at key_idx."""
    keys = env.get("api_keys") or [env.get("api_key", "")]
    key = keys[key_idx] if 0 <= key_idx < len(keys) else (keys[0] if keys else "")
    return {**env, "api_key": key}


def immich_headers(env):
    return {"x-api-key": env["api_key"], "Accept": "application/json"}


def _key_env_for_asset(env, asset_id):
    """Return env with the api_key that owns the given asset_id."""
    with _asset_key_lock:
        key_idx = _asset_key_map.get(asset_id, 0)
    return env_for_key(env, key_idx)


def env_for_asset_verified(base_env, asset_id, preferred_idx=0):
    """Resolve ownership explicitly; shared readability never grants replacement rights."""
    keys = base_env.get("api_keys") or []
    owners = {item["key_idx"]: item["id"] for item in fetch_key_owners(base_env)}
    candidates = list(range(len(keys)))
    if preferred_idx in candidates:
        candidates.remove(preferred_idx)
        candidates.insert(0, preferred_idx)
    for idx in candidates:
        env = env_for_key(base_env, idx)
        asset = fetch_asset(env, asset_id)
        if asset and asset.get("ownerId") and asset["ownerId"] == owners.get(idx):
            with _asset_key_lock:
                _asset_key_map[asset_id] = idx
            return env
    raise StructuredSearchError("No configured owner API key can access this asset.")


_user_cache = {"ts": 0.0, "users": [], "by_id": {}}
_key_owner_cache = {"ts": 0.0, "owners": []}
_user_lock = threading.Lock()


def fetch_users(env, force=False):
    """Return cached user list (id/name/email), refreshing every TTL seconds.

    Queries every API key and merges results, deduplicating by user ID.
    """
    now = time.time()
    with _user_lock:
        if not force and _user_cache["users"] and (now - _user_cache["ts"]) < USER_CACHE_TTL:
            return _user_cache["users"]

    seen_ids: set = set()
    users = []
    api_keys = env.get("api_keys") or ([env["api_key"]] if env.get("api_key") else [])
    for key in api_keys:
        if not key or not env["url"]:
            continue
        key_env = {**env, "api_key": key}
        for path in ("/api/users", "/api/users?isAll=true", "/api/user"):
            try:
                resp = requests.get(env["url"] + path, headers=immich_headers(key_env),
                                    timeout=HTTP_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and "users" in data:
                        data = data["users"]
                    if isinstance(data, list):
                        for u in data:
                            uid = u.get("id")
                            if uid and uid not in seen_ids:
                                seen_ids.add(uid)
                                users.append({
                                    "id": uid,
                                    "name": u.get("name") or u.get("email") or "",
                                    "email": u.get("email") or "",
                                })
                        break
            except requests.RequestException:
                continue

    by_id = {u["id"]: u for u in users if u.get("id")}
    with _user_lock:
        _user_cache["ts"] = now
        _user_cache["users"] = users
        _user_cache["by_id"] = by_id
    return users


def fetch_key_owners(env, force=False):
    """Return [{key_idx, id, name, email}] — the owner user of each configured
    API key (via /api/users/me per key). Cached like the user list. Lets the UI
    offer a per-key (per-user) selection of which libraries to scan."""
    now = time.time()
    with _user_lock:
        if (not force and _key_owner_cache.get("scope") == (env["url"], tuple(env.get("api_keys") or []))
                and _key_owner_cache["owners"] and all(owner["id"] for owner in _key_owner_cache["owners"])
                and (now - _key_owner_cache["ts"]) < USER_CACHE_TTL):
            return _key_owner_cache["owners"]

    owners = []
    api_keys = env.get("api_keys") or []
    for idx, key in enumerate(api_keys):
        info = {}
        if key and env["url"]:
            key_env = {**env, "api_key": key}
            for path in ("/api/users/me", "/api/user/me"):
                try:
                    resp = requests.get(env["url"] + path, headers=immich_headers(key_env),
                                        timeout=HTTP_TIMEOUT)
                    if resp.status_code == 200:
                        candidate = resp.json()
                        if isinstance(candidate, dict) and candidate.get("id"):
                            info = candidate
                            break
                except (requests.RequestException, ValueError):
                    continue
        owners.append({
            "key_idx": idx,
            "id": info.get("id") or "",
            "name": info.get("name") or info.get("email") or f"Key {idx + 1}",
            "email": info.get("email") or "",
        })
    with _user_lock:
        _key_owner_cache["scope"] = (env["url"], tuple(env.get("api_keys") or []))
        _key_owner_cache["ts"] = now
        _key_owner_cache["owners"] = owners
    return owners


def owner_name_for(env, owner_id):
    if not owner_id:
        return ""
    with _user_lock:
        cached = _user_cache["by_id"].get(owner_id)
    if cached:
        return cached["name"]
    fetch_users(env)
    with _user_lock:
        cached = _user_cache["by_id"].get(owner_id)
    return cached["name"] if cached else ""




DOWNLOAD_RETRIES = 3   # attempts for a single original download


def download_original(env, asset_id, dest):
    """Download an asset's original bytes to dest. Returns (ok, reason): (True, "")
    on success, else (False, <reason>). Transient failures (network errors, 5xx)
    are retried with backoff; a cancel or a permanent 4xx stops immediately."""
    url = f"{env['url']}/api/assets/{asset_id}/original"
    reason = "unknown error"
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        if is_cancelled(asset_id):
            return False, "cancelled"
        try:
            with requests.get(url, headers=immich_headers(env), stream=True, timeout=300) as resp:
                if resp.status_code != 200:
                    reason = f"Immich HTTP {resp.status_code}"
                    if 400 <= resp.status_code < 500:
                        return False, reason          # permanent — don't retry
                else:
                    with open(dest, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if is_cancelled(asset_id):
                                return False, "cancelled"
                            if chunk:
                                fh.write(chunk)
                    return True, ""
        except OSError as exc:
            return False, f"Write error: {exc}"         # local disk issue — don't retry
        except requests.RequestException as exc:
            reason = type(exc).__name__                # e.g. ConnectionError, ReadTimeout
        if attempt < DOWNLOAD_RETRIES:
            time.sleep(min(2 ** attempt, 8))           # 2s, 4s, …
    return False, reason


def get_asset_meta(env, asset_id):
    """Fetch dates and capture metadata that copyAsset does not copy."""
    try:
        r = requests.get(f"{env['url']}/api/assets/{asset_id}",
                         headers=immich_headers(env), timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            a = r.json()
            return {"fileCreatedAt": a.get("fileCreatedAt"),
                    "fileModifiedAt": a.get("fileModifiedAt"),
                    "exifInfo": a.get("exifInfo") or {}}
    except (requests.RequestException, ValueError):
        pass
    return {}


def capture_metadata_fields(meta):
    """Build updateAsset fields from the original Immich record."""
    exif = meta.get("exifInfo") or {}
    date = exif.get("dateTimeOriginal") or meta.get("fileCreatedAt")
    if not date:
        return {}

    # Immich returns the instant in UTC and the original offset separately.
    # The single-asset update API derives its timezone from the date's offset.
    try:
        captured = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return {}
    zone_name = exif.get("timeZone")
    if zone_name and captured.tzinfo is not None:
        match = re.fullmatch(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?", zone_name)
        if match:
            hours, minutes = int(match[2]), int(match[3] or 0)
            if hours <= 23 and minutes < 60:
                delta = timedelta(hours=hours, minutes=minutes)
                captured = captured.astimezone(timezone(delta if match[1] == "+" else -delta))
        else:
            try:
                captured = captured.astimezone(ZoneInfo(zone_name))
            except ZoneInfoNotFoundError:
                pass

    fields = {"dateTimeOriginal": captured.isoformat()}
    for key in ("latitude", "longitude"):
        if exif.get(key) is not None:
            fields[key] = exif[key]
    return fields


def upload_new_asset(env, path, name, file_created_at, file_modified_at, mime):
    """Upload `path` as a NEW Immich asset. Returns (new_id, status) where status
    is 'created' or 'duplicate', or (None, error_string) on failure."""
    url = f"{env['url']}/api/assets"
    now = utcnow_iso()
    device_asset_id = f"recompress-{int(time.time()*1000)}"
    try:
        with open(path, "rb") as fh:
            files = {"assetData": (name, fh, mime)}
            data = {"deviceAssetId": device_asset_id,
                    "deviceId": "immich-recompress-ui",
                    "fileCreatedAt": file_created_at or now,
                    "fileModifiedAt": file_modified_at or now,
                    "filename": name}
            resp = requests.post(url, headers={"x-api-key": env["api_key"]},
                                 files=files, data=data, timeout=600)
        if resp.status_code not in (200, 201):
            return None, f"Upload HTTP {resp.status_code}"
        body = resp.json()
        return body.get("id"), body.get("status")
    except (requests.RequestException, OSError, ValueError) as exc:
        return None, str(exc)


def get_asset_tag_ids(env, asset_id):
    """Return the list of tag IDs attached to an asset (or [] on failure)."""
    url = f"{env['url']}/api/assets/{asset_id}"
    try:
        r = requests.get(url, headers=immich_headers(env), timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return [t.get("id") for t in (r.json().get("tags") or []) if t.get("id")]
    except (requests.RequestException, ValueError):
        pass
    return []


def copy_asset_tags(env, source_id, target_id):
    """Replicate the source asset's tags onto the target via bulkTagAssets.

    copyAsset does NOT carry tags, so we copy them explicitly. Both assets live
    on the same Immich instance, so we reuse the existing tag IDs directly.
    Returns True on success, and True when the source has no tags (nothing to do)."""
    tag_ids = get_asset_tag_ids(env, source_id)
    if not tag_ids:
        return True
    url = f"{env['url']}/api/tags/assets"
    payload = {"assetIds": [target_id], "tagIds": tag_ids}
    headers = {**immich_headers(env), "Content-Type": "application/json"}
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
        return r.status_code in (200, 201, 204)
    except requests.RequestException:
        return False


def copy_asset_metadata(env, source_id, target_id):
    """Copy albums / favorite / shared links / stack / sidecar from the old asset
    (source) to the new one (target) via copyAsset, then copy tags separately
    (copyAsset does not carry tags). Returns True only if both steps succeed."""
    url = f"{env['url']}/api/assets/copy"
    payload = {"sourceId": source_id, "targetId": target_id,
               "sharedLinks": True, "albums": True, "sidecar": True,
               "stack": True, "favorite": True}
    headers = {**immich_headers(env), "Content-Type": "application/json"}
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
        if r.status_code not in (200, 201, 204):
            return False
    except requests.RequestException:
        return False
    return copy_asset_tags(env, source_id, target_id)


def trash_asset(env, asset_id):
    """Move an asset to the Immich trash (force=False → recoverable). True/False."""
    url = f"{env['url']}/api/assets"
    payload = {"ids": [asset_id], "force": False}
    headers = {**immich_headers(env), "Content-Type": "application/json"}
    try:
        r = requests.delete(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
        return r.status_code in (200, 204)
    except requests.RequestException:
        return False


def fetch_asset(env, asset_id):
    """Return the full asset JSON (AssetResponseDto) or {} on failure."""
    url = f"{env['url']}/api/assets/{asset_id}"
    try:
        r = requests.get(url, headers=immich_headers(env), timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return {}


def update_asset(env, asset_id, **fields):
    """PUT updateAsset with the given fields (e.g. livePhotoVideoId, visibility).
    Returns True/False."""
    url = f"{env['url']}/api/assets/{asset_id}"
    headers = {**immich_headers(env), "Content-Type": "application/json"}
    try:
        r = requests.put(url, headers=headers, json=fields, timeout=HTTP_TIMEOUT)
        return r.status_code in (200, 201, 204)
    except requests.RequestException:
        return False


def set_live_photo_link(env, image_id, video_id):
    """Set (video_id) or clear (video_id=None) the motion-video link on a still
    image via updateAsset. Returns True/False."""
    return update_asset(env, image_id, livePhotoVideoId=video_id)



def detect_immich_version(env):
    if not env["url"] or not env["api_key"]:
        return None
    for path in ("/api/server/version", "/api/server-info/version", "/api/server-info"):
        try:
            resp = requests.get(env["url"] + path, headers=immich_headers(env), timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    if {"major", "minor", "patch"} <= set(data.keys()):
                        return f"{data['major']}.{data['minor']}.{data['patch']}"
                    ver = data.get("version") or data.get("serverVersion")
                    if ver:
                        return str(ver)
        except (requests.RequestException, ValueError):
            continue
    return None


def supports_structured_search(env):
    """Whether the server version supports the v3.2 cursor/filter search shape."""
    version = detect_immich_version(env)
    if not version:
        return False
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match:
        return False
    return tuple(int(part or 0) for part in match.groups()) >= (3, 2, 0)


def asset_codec(asset):
    info = asset.get("exifInfo") or {}
    media = asset.get("originalMimeType")
    return info.get("codec") or info.get("videoCodec") or media or ""


UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class StructuredSearchError(RuntimeError):
    """An Immich metadata search that cannot safely provide all results."""


def norm_media(value):
    """Normalise a media-type query/body value to one of the supported types."""
    m = str(value or "video").lower()
    return m if m in ("image", "motionphoto") else "video"


def parse_key_indices(value, n):
    """Parse a comma-separated list of API-key indices into valid indices in
    [0, n). Empty / missing / nothing-valid → all indices (full scan)."""
    if not value:
        return list(range(n))
    out = []
    for part in str(value).split(","):
        part = part.strip()
        try:
            i = int(part)
        except ValueError:
            continue
        if 0 <= i < n and i not in out:
            out.append(i)
    return out or list(range(n))


def _search_page(env, body):
    """Read and validate a search page; never turn an invalid response into zero matches."""
    try:
        response = requests.post(env["url"] + "/api/search/metadata",
                                 headers=immich_headers(env), json=body, timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            raise StructuredSearchError(f"Immich metadata search returned HTTP {response.status_code}. Check the server version and API-key permissions.")
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise StructuredSearchError("Could not complete the Immich metadata search. Check the server and API-key permissions.") from exc
    block = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(block, dict) or not isinstance(block.get("items"), list):
        raise StructuredSearchError("Immich returned an invalid metadata-search response.")
    if any(not isinstance(item, dict) or not item.get("id") for item in block["items"]):
        raise StructuredSearchError("Immich returned an invalid metadata-search asset.")
    return block


def search_metadata_all(env, body, max_pages=100):
    """Return a complete legacy scan or fail, including on page-limit exhaustion."""
    items, seen = [], set()
    page = 1
    for _ in range(max_pages):
        req = dict(body, page=page)
        req.setdefault("size", IMMICH_PER_PAGE)
        block = _search_page(env, req)
        items.extend(block["items"])
        next_page = block.get("nextPage")
        if "nextPage" not in block:
            raise StructuredSearchError("Immich omitted metadata-search pagination.")
        if next_page is None:
            return items
        try:
            next_page = int(next_page)
        except (ValueError, TypeError) as exc:
            raise StructuredSearchError("Immich returned an invalid search page token.") from exc
        if next_page <= page or next_page in seen or not block["items"]:
            raise StructuredSearchError("Immich metadata-search pagination did not advance.")
        seen.add(next_page)
        page = next_page
    raise StructuredSearchError(f"The search exceeded the {max_pages}-page scan limit. Narrow the filters.")


def search_metadata_filtered(env, asset_filter, max_pages=100):
    """Return every result from Immich's cursor-based structured search API.

    The structured and legacy search request formats are mutually exclusive.
    Date-constrained scans use this helper so their requested constraints are
    never silently dropped when a server rejects the newer API shape.
    """
    items, seen = [], set()
    cursor = None
    for _ in range(max_pages):
        body = {"filter": asset_filter, "withExif": True, "size": IMMICH_PER_PAGE}
        if cursor:
            body["cursor"] = cursor
        block = _search_page(env, body)
        items.extend(block["items"])
        if "nextCursor" not in block:
            raise StructuredSearchError("Immich omitted structured-search pagination. Immich 3.2+ is required.")
        cursor = block["nextCursor"]
        if cursor is None:
            return items
        if not isinstance(cursor, str) or not cursor or cursor in seen or not block["items"]:
            raise StructuredSearchError("Immich metadata-search pagination did not advance.")
        seen.add(cursor)
    raise StructuredSearchError(
        f"The search exceeded the {max_pages}-page scan limit. Narrow the filters.")


def motion_summary(env, still, vasset, vsize):
    """Build a list summary for a Live/motion photo. `size`/`potential` are the
    motion-video size — i.e. the bytes that stripping would reclaim."""
    info = still.get("exifInfo") or {}
    width = info.get("exifImageWidth")
    height = info.get("exifImageHeight")
    vdur = parse_duration((vasset or {}).get("duration")) or 0
    aid = still.get("id")
    with _state_lock:
        job = _jobs.get(aid)
        status = job["status"] if job else "idle"
    return {
        "id": aid,
        "media": "motionphoto",
        "name": still.get("originalFileName") or still.get("originalPath") or "",
        "size": vsize,
        "size_human": human_size(vsize),
        "potential": vsize,
        "duration": vdur,
        "duration_human": human_duration(vdur),
        "codec": "MOTION",
        "resolution": f"{width}x{height}" if width and height else "",
        "bitrate": None,
        "date": still.get("fileCreatedAt") or still.get("createdAt"),
        "owner_id": still.get("ownerId"),
        "owner_name": owner_name_for(env, still.get("ownerId")),
        "is_favorite": bool(still.get("isFavorite")),
        "is_archived": bool(still.get("isArchived")),
        "albums": [],
        "people": [p.get("name") for p in (still.get("people") or []) if p.get("name")],
        "status": status,
    }


def collect_motion_photos(env, key_indices, min_bytes, user_filter, search_filter,
                          capture_filter=None, legacy_capture_filter=None):
    """List Live/motion photos. The still IMAGE asset carries livePhotoVideoId;
    the reclaimable bytes live on the hidden VIDEO component. Returns summaries
    whose `size` is the motion-video size (what stripping would free)."""
    # Hidden video components keyed by id, so we can look up sizes without an
    # extra request per photo. A capture-date filter applies to the still, not
    # its linked clip, so only fetch the linked clips for matching stills.
    video_map = {}
    has_capture_filter = capture_filter is not None or legacy_capture_filter is not None
    if not has_capture_filter:
        for key_idx in key_indices:
            key_env = env_for_key(env, key_idx)
            for v in search_metadata_all(key_env, {"type": "VIDEO", "visibility": "hidden",
                                                   "withExif": True}):
                vid = v.get("id")
                if vid:
                    video_map[vid] = v

    summaries = []
    for key_idx in key_indices:
        key_env = env_for_key(env, key_idx)
        if capture_filter is None:
            body = {"type": "IMAGE", "isMotion": True, "withExif": True}
            if user_filter:
                body["ownerId"] = user_filter
            if legacy_capture_filter:
                body.update(legacy_capture_filter)
            stills = search_metadata_all(key_env, body)
        else:
            motion_filter = {**capture_filter, "isMotion": {"eq": True}}
            stills = search_metadata_filtered(key_env, motion_filter)
        for still in stills:
            vid = still.get("livePhotoVideoId")
            if not vid:
                continue
            vasset = video_map.get(vid) or fetch_asset(key_env, vid)
            vinfo = (vasset or {}).get("exifInfo") or {}
            try:
                vsize = int(vinfo.get("fileSizeInByte") or (vasset or {}).get("fileSizeInByte") or 0)
            except (TypeError, ValueError):
                vsize = 0
            aid = still.get("id")
            if aid:
                with _asset_key_lock:
                    _asset_key_map[aid] = key_idx
            summaries.append(motion_summary(key_env, still, vasset, vsize))

    out = []
    for s in summaries:
        if s["size"] < min_bytes:
            continue
        if user_filter and s["owner_id"] != user_filter:
            continue
        if search_filter and search_filter not in (s["name"] or "").lower():
            continue
        out.append(s)
    return out


def paginate_summaries(summaries, sort, order, page, per_page):
    """Sort, total and paginate a list of asset summaries into the list response."""
    reverse = order == "desc"
    if sort == "size":
        summaries.sort(key=lambda v: v["size"], reverse=reverse)
    elif sort == "duration":
        summaries.sort(key=lambda v: v["duration"], reverse=reverse)
    elif sort == "savings":
        summaries.sort(key=lambda v: v["potential"], reverse=reverse)
    elif sort == "name":
        summaries.sort(key=lambda v: (v["name"] or "").lower(), reverse=reverse)
    elif sort == "date":
        summaries.sort(key=lambda v: (v["date"] or ""), reverse=reverse)
    total = len(summaries)
    start = (page - 1) * per_page
    end = start + per_page
    return jsonify({
        "total": total,
        "total_size": sum(v["size"] for v in summaries),
        "total_potential": sum(v["potential"] for v in summaries),
        "page": page,
        "per_page": per_page,
        "assets": summaries[start:end],
    })


def video_summary(env, asset, media="video"):
    info = asset.get("exifInfo") or {}
    size = info.get("fileSizeInByte") or asset.get("fileSizeInByte") or 0
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0
    duration = parse_duration(asset.get("duration"))
    width = info.get("exifImageWidth")
    height = info.get("exifImageHeight")
    resolution = f"{width}x{height}" if width and height else ""
    if media == "image":
        mime = asset.get("originalMimeType") or info.get("mimeType") or ""
        codec = mime.split("/")[-1].upper() if mime else ""
    else:
        codec = asset_codec(asset)
    asset_id = asset.get("id")
    with _state_lock:
        job = _jobs.get(asset_id)
        status = job["status"] if job else "idle"
    # Rough potential-savings estimate (bytes) for dashboard + "savings" sort.
    # Videos already in an efficient codec (HEVC/AV1) have little to gain.
    cl = (codec or "").lower()
    if media == "image":
        frac = 0.50
    elif cl in ("hevc", "av1"):
        frac = 0.08
    else:
        frac = 0.50
    potential = int(size * frac)
    return {
        "id": asset_id,
        "media": media,
        "name": asset.get("originalFileName") or asset.get("originalPath") or "",
        "size": size,
        "size_human": human_size(size),
        "potential": potential,
        "duration": duration,
        "duration_human": human_duration(duration),
        "codec": codec,
        "resolution": resolution,
        "bitrate": info.get("bitrate") or info.get("bitRate"),
        "date": asset.get("fileCreatedAt") or asset.get("createdAt"),
        "owner_id": asset.get("ownerId"),
        "owner_name": owner_name_for(env, asset.get("ownerId")),
        "is_favorite": bool(asset.get("isFavorite")),
        "is_archived": bool(asset.get("isArchived")),
        "albums": [],
        "people": [p.get("name") for p in (asset.get("people") or []) if p.get("name")],
        "status": status,
    }
