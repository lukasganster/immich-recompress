"""HTTP routes (Flask blueprint): static serving, status, asset listing,
thumbnail/download proxies, queue management and the SSE stream.
"""
import json
import os
import queue
import shutil
import threading
import time

import requests
from flask import (
    Blueprint, Response, jsonify, request, send_file, send_from_directory,
    stream_with_context,
)

from backend.config import (
    BACKUP_DIR, FRONTEND_PER_PAGE, HTTP_TIMEOUT, IMMICH_PER_PAGE, STATIC_DIR,
    get_env, human_duration, human_size, parse_duration, utcnow_iso,
)
from backend.state import (
    _asset_key_lock, _asset_key_map, _jobs, _queue_order, _state_lock,
    _sub_lock, _subscribers, _work_queue, active_id, kill_proc,
)
from backend.db import db_delete_jobs
from backend.immich_api import (
    UUID_RE, asset_codec, collect_motion_photos, detect_immich_version,
    env_for_asset_verified, env_for_key, fetch_asset, fetch_key_owners,
    fetch_users, immich_headers, motion_summary, norm_media, owner_name_for,
    paginate_summaries, parse_key_indices, search_metadata_all, video_summary,
    _key_env_for_asset,
)
from backend.jobs import (
    broadcast, broadcast_queue_update, cleanup_temp, compute_stats,
    do_recompress_motion, do_replace, do_strip_motion, emit_job_update,
    new_job, persist_if_terminal, public_job, update_job,
)

bp = Blueprint("main", __name__)

@bp.before_request
def _validate_asset_id():
    """Reject any non-UUID `asset_id` path segment before it reaches the
    filesystem (src/backup paths) or the upstream Immich URL builders. This
    closes path-traversal and request-injection via the `<asset_id>` route var.
    """
    asset_id = (request.view_args or {}).get("asset_id")
    if asset_id is not None and not UUID_RE.fullmatch(str(asset_id)):
        return jsonify({"error": "Invalid asset id"}), 400




@bp.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@bp.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


# --------------------------------------------------------------------------- #
# Routes: status
# --------------------------------------------------------------------------- #

@bp.route("/api/status")
def api_status():
    env = get_env()
    handbrake = shutil.which("HandBrakeCLI") is not None
    ffprobe = shutil.which("ffprobe") is not None
    ffmpeg = shutil.which("ffmpeg") is not None
    n_keys = len(env.get("api_keys") or [])
    api_key_set = n_keys > 0
    immich_version = detect_immich_version(env)
    ok = bool(env["url"]) and api_key_set
    msg = "OK" if ok else "Missing configuration (IMMICH_URL / IMMICH_API_KEY)"
    return jsonify({
        "ok": ok,
        "msg": msg,
        "env": {
            "IMMICH_URL": env["url"],
            "api_key_set": api_key_set,
            "api_key_count": n_keys,
        },
        "handbrake": handbrake,
        "ffprobe": ffprobe,
        "ffmpeg": ffmpeg,
        "immich_version": immich_version,
    })


# --------------------------------------------------------------------------- #
# Routes: videos
# --------------------------------------------------------------------------- #

@bp.route("/api/assets")
def api_assets():
    env = get_env()
    page = max(1, request.args.get("page", 1, type=int))
    media = norm_media(request.args.get("media"))
    immich_type = "IMAGE" if media in ("image", "motionphoto") else "VIDEO"
    sort = request.args.get("sort", "size")
    order = request.args.get("order", "desc")
    codec_filter = (request.args.get("codec") or "").strip().lower()
    user_filter = (request.args.get("user") or "").strip()
    search_filter = (request.args.get("search") or "").strip().lower()
    min_gb_param = request.args.get("min_gb")
    min_mb_param = request.args.get("min_mb")

    per_page = request.args.get("per_page", FRONTEND_PER_PAGE, type=int)
    if per_page not in (10, 25, 50, 100):
        per_page = FRONTEND_PER_PAGE

    other_codec = media == "video" and codec_filter in ("__other__", "other")

    if sort not in ("size", "date", "name", "duration", "savings"):
        sort = "size"
    if order not in ("asc", "desc"):
        order = "desc"

    if min_mb_param not in (None, ""):
        try:
            min_bytes = int(float(min_mb_param) * 1024 ** 2)
        except (TypeError, ValueError):
            min_bytes = 0
    else:
        try:
            min_gb = float(min_gb_param) if min_gb_param not in (None, "") else 0.0
        except (TypeError, ValueError):
            min_gb = 0.0
        min_bytes = int(min_gb * 1024 ** 3)

    api_keys = env.get("api_keys") or []
    if not env["url"] or not api_keys:
        return jsonify({"total": 0, "page": page, "per_page": per_page,
                        "assets": [], "error": "Immich not configured"})

    # `keys` restricts the scan to a subset of API keys (by index); empty = all.
    key_indices = parse_key_indices(request.args.get("keys"), len(api_keys))

    fetch_users(env)

    if media == "motionphoto":
        summaries = collect_motion_photos(env, key_indices, min_bytes, user_filter, search_filter)
        return paginate_summaries(summaries, sort, order, page, per_page)

    # collected is a list of (asset_dict, key_idx) tuples
    collected: list = []
    for key_idx in key_indices:
        key_env = env_for_key(env, key_idx)
        immich_page = 1
        while True:
            body = {"type": immich_type, "size": IMMICH_PER_PAGE, "page": immich_page,
                    "withExif": True}
            if user_filter:
                body["ownerId"] = user_filter
            try:
                resp = requests.post(env["url"] + "/api/search/metadata",
                                     headers=immich_headers(key_env), json=body,
                                     timeout=HTTP_TIMEOUT)
            except requests.RequestException as exc:
                # If all keys fail we'll return an empty list; partial results are
                # still useful so continue to the next key instead of aborting.
                break
            if resp.status_code != 200:
                break
            data = resp.json()
            assets_block = (data.get("assets") or {}) if isinstance(data, dict) else {}
            items = assets_block.get("items", [])
            next_page = assets_block.get("nextPage")
            for asset in items:
                collected.append((asset, key_idx))
            if not next_page or not items:
                break
            immich_page += 1
            if immich_page > 100:
                break

    # Populate the asset→key map so per-asset operations use the right key.
    with _asset_key_lock:
        for asset, key_idx in collected:
            aid = asset.get("id")
            if aid:
                _asset_key_map[aid] = key_idx

    known_codecs = ("h264", "hevc", "av1")
    summaries = []
    for asset, key_idx in collected:
        key_env = env_for_key(env, key_idx)
        summary = video_summary(key_env, asset, media=media)
        if summary["size"] < min_bytes:
            continue
        if media == "video":
            codec_value = (summary["codec"] or "").lower()
            if other_codec:
                if codec_value in known_codecs:
                    continue
            elif codec_filter and codec_value != codec_filter:
                continue
        if user_filter and summary["owner_id"] != user_filter:
            continue
        if search_filter and search_filter not in (summary["name"] or "").lower():
            continue
        summaries.append(summary)

    return paginate_summaries(summaries, sort, order, page, per_page)


def resolve_asset_summary(env, api_keys, asset_id):
    """Fetch a single asset by id (trying each API key) and build a list summary
    with its correct media type — video, image, or motionphoto (a still image
    that carries a hidden motion video). Returns None if not found on any key."""
    for key_idx in range(len(api_keys)):
        key_env = env_for_key(env, key_idx)
        asset = fetch_asset(key_env, asset_id)
        if not asset or not asset.get("id"):
            continue
        with _asset_key_lock:
            _asset_key_map[asset_id] = key_idx
        atype = (asset.get("type") or "").upper()
        if atype == "IMAGE" and asset.get("livePhotoVideoId"):
            vasset = fetch_asset(key_env, asset["livePhotoVideoId"])
            vinfo = (vasset or {}).get("exifInfo") or {}
            try:
                vsize = int(vinfo.get("fileSizeInByte") or (vasset or {}).get("fileSizeInByte") or 0)
            except (TypeError, ValueError):
                vsize = 0
            return motion_summary(key_env, asset, vasset, vsize)
        if atype == "IMAGE":
            return video_summary(key_env, asset, media="image")
        return video_summary(key_env, asset, media="video")
    return None


@bp.route("/api/resolve", methods=["POST"])
def api_resolve():
    """Resolve assets referenced by Immich URL, /photos/<id>, or bare id into list
    summaries, so specific assets can be added without the threshold listing."""
    env = get_env()
    api_keys = env.get("api_keys") or []
    if not env["url"] or not api_keys:
        return jsonify({"assets": [], "errors": [], "error": "Immich not configured"}), 503
    body = request.get_json(silent=True) or {}
    items = body.get("items") or []
    if isinstance(items, str):
        items = [items]
    fetch_users(env)

    assets, errors, seen = [], [], set()
    for raw in items:
        s = str(raw or "").strip()
        if not s:
            continue
        match = UUID_RE.search(s)
        if not match:
            errors.append({"input": s, "reason": "no asset id found"})
            continue
        aid = match.group(0).lower()
        if aid in seen:
            continue
        seen.add(aid)
        summary = resolve_asset_summary(env, api_keys, aid)
        if summary:
            assets.append(summary)
        else:
            errors.append({"input": s, "reason": "not found"})
    return jsonify({"assets": assets, "errors": errors})


@bp.route("/api/asset/<asset_id>")
def api_asset_detail(asset_id):
    env = get_env()
    if not env["url"] or not env.get("api_keys"):
        return jsonify({"error": "Immich not configured"}), 503
    key_env = _key_env_for_asset(env, asset_id)
    try:
        resp = requests.get(env["url"] + f"/api/assets/{asset_id}",
                            headers=immich_headers(key_env), timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502
    if resp.status_code != 200:
        return jsonify({"error": f"Immich {resp.status_code}"}), resp.status_code
    asset = resp.json()
    info = asset.get("exifInfo") or {}

    size = info.get("fileSizeInByte") or 0
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0
    duration = parse_duration(asset.get("duration"))
    width = info.get("exifImageWidth")
    height = info.get("exifImageHeight")

    owner = asset.get("owner") or {}
    owner_id = asset.get("ownerId") or owner.get("id")
    owner_name = owner.get("name") or owner_name_for(env, owner_id)

    albums = [{"id": a.get("id"), "name": a.get("albumName") or a.get("name")}
              for a in (asset.get("albums") or [])]
    people = [{"id": p.get("id"), "name": p.get("name")}
              for p in (asset.get("people") or [])]
    tags = [t.get("name") for t in (asset.get("tags") or []) if t.get("name")]

    with _state_lock:
        job = _jobs.get(asset_id)
        job_status = {
            "status": job["status"] if job else "idle",
            "progress": job["progress"] if job else 0.0,
            "log": job["log"] if job else "",
        }

    return jsonify({
        "id": asset.get("id"),
        "name": asset.get("originalFileName") or "",
        "original_path": asset.get("originalPath") or "",
        "mime_type": asset.get("originalMimeType") or info.get("mimeType") or "",
        "checksum": asset.get("checksum") or "",
        "size": size,
        "size_human": human_size(size),
        "duration": duration,
        "duration_human": human_duration(duration),
        "exif": {
            "codec": asset_codec(asset),
            "resolution": f"{width}x{height}" if width and height else "",
            "width": width,
            "height": height,
            "bitrate": info.get("bitrate") or info.get("bitRate"),
            "fps": info.get("fps") or info.get("framerate"),
            "make": info.get("make"),
            "model": info.get("model"),
            "lens": info.get("lensModel"),
            "lat": info.get("latitude"),
            "lon": info.get("longitude"),
            "city": info.get("city"),
            "country": info.get("country"),
            "orientation": info.get("orientation"),
        },
        "owner": {"id": owner_id, "name": owner_name, "email": owner.get("email") or ""},
        "albums": albums,
        "people": people,
        "tags": tags,
        "is_favorite": bool(asset.get("isFavorite")),
        "is_archived": bool(asset.get("isArchived")),
        "is_trashed": bool(asset.get("isTrashed")),
        "created_at": asset.get("fileCreatedAt") or asset.get("createdAt"),
        "updated_at": asset.get("updatedAt"),
        "job_status": job_status,
    })


# --------------------------------------------------------------------------- #
# Routes: users
# --------------------------------------------------------------------------- #

@bp.route("/api/users")
def api_users():
    env = get_env()
    users = fetch_users(env)
    return jsonify({"users": users})


@bp.route("/api/keys")
def api_key_owners():
    """Owner user of each configured API key, for the per-key library selector."""
    env = get_env()
    return jsonify({"owners": fetch_key_owners(env)})


# --------------------------------------------------------------------------- #
# Routes: thumbnail & download proxies
# --------------------------------------------------------------------------- #

@bp.route("/api/thumbnail/<asset_id>")
def api_thumbnail(asset_id):
    env = get_env()
    if not env["url"] or not env.get("api_keys"):
        return Response("Not configured", status=503)
    key_env = _key_env_for_asset(env, asset_id)
    size = request.args.get("size", "preview")
    candidates = [
        (env["url"] + f"/api/assets/{asset_id}/thumbnail", {"size": size}),
        (env["url"] + f"/api/asset/thumbnail/{asset_id}", {"size": size}),
    ]
    for url, params in candidates:
        try:
            resp = requests.get(url, headers=immich_headers(key_env), params=params,
                                stream=True, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            return Response(stream_with_context(resp.iter_content(chunk_size=65536)),
                            content_type=content_type)
    return Response("Thumbnail unavailable", status=502)


@bp.route("/api/download/<asset_id>")
def api_download(asset_id):
    env = get_env()
    if not env["url"] or not env.get("api_keys"):
        return Response("Not configured", status=503)
    key_env = _key_env_for_asset(env, asset_id)

    filename = request.args.get("name")
    if not filename:
        try:
            meta = requests.get(env["url"] + f"/api/assets/{asset_id}",
                                headers=immich_headers(key_env), timeout=HTTP_TIMEOUT)
            if meta.status_code == 200:
                filename = meta.json().get("originalFileName")
        except requests.RequestException:
            filename = None
    filename = filename or f"{asset_id}.bin"

    url = env["url"] + f"/api/assets/{asset_id}/original"
    try:
        upstream = requests.get(url, headers=immich_headers(key_env), stream=True, timeout=600)
    except requests.RequestException as exc:
        return Response(f"Download failed: {exc}", status=502)
    if upstream.status_code != 200:
        return Response(f"Immich {upstream.status_code}", status=upstream.status_code)

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if upstream.headers.get("Content-Length"):
        headers["Content-Length"] = upstream.headers["Content-Length"]

    return Response(stream_with_context(upstream.iter_content(chunk_size=1024 * 1024)),
                    content_type=content_type, headers=headers)


# --------------------------------------------------------------------------- #
# Routes: queue management
# --------------------------------------------------------------------------- #

@bp.route("/api/enqueue", methods=["POST"])
def api_enqueue():
    env = get_env()
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        ids = []

    # Per-asset media overrides (id → media). Assets added by link/ID can be a
    # different type than the current browse mode, so each job uses its own type.
    medias = body.get("medias") if isinstance(body.get("medias"), dict) else {}
    default_media = norm_media(body.get("media"))

    params = {
        "media": default_media,
        "encoder": body.get("encoder", "x265"),
        "quality": body.get("quality", 24),
        "photo_target_savings": body.get("photo_target_savings", 40),
        "compress_raw": bool(body.get("compress_raw", False)),
        "preset": body.get("preset", "medium"),
        "resolution": body.get("resolution", "original"),
        "motion_action": body.get("motion_action", "remove"),
        "skip_codecs": body.get("skip_codecs", "hevc,av1"),
        "min_savings": body.get("min_savings", 10),
        "replace": bool(body.get("replace", True)),
        "confirm": bool(body.get("confirm", True)),
        "download_only": bool(body.get("download_only", False)),
        "backup_dir": (body.get("backup_dir") or "").strip() or BACKUP_DIR,
    }

    added = []
    for asset_id in ids:
        # Only accept genuine Immich asset UUIDs — ids flow into filesystem
        # paths and upstream URLs in the worker, so reject anything else.
        if not asset_id or not UUID_RE.fullmatch(str(asset_id)):
            continue
        with _state_lock:
            existing = _jobs.get(asset_id)
            if existing and existing["status"] in ("queued", "downloading",
                                                   "encoding", "replacing", "review"):
                continue
            job_params = dict(params)
            if asset_id in medias:
                job_params["media"] = norm_media(medias.get(asset_id))
            job_params["name"] = body.get("names", {}).get(asset_id, asset_id) if isinstance(body.get("names"), dict) else asset_id
            sizes = body.get("sizes")
            job_params["size"] = sizes.get(asset_id) if isinstance(sizes, dict) else None
            with _asset_key_lock:
                job_params["_key_idx"] = _asset_key_map.get(asset_id, 0)
            _jobs[asset_id] = new_job(asset_id, job_params)
            if asset_id not in _queue_order:
                _queue_order.append(asset_id)
        _work_queue.put(asset_id)
        added.append(asset_id)

    broadcast_queue_update()
    return jsonify({"added": added})


@bp.route("/api/cancel/<asset_id>", methods=["POST"])
def api_cancel(asset_id):
    with _state_lock:
        job = _jobs.get(asset_id)
        if not job:
            return jsonify({"ok": False, "error": "Unknown job"}), 404
        status = job["status"]
        if status not in ("queued", "downloading", "encoding"):
            return jsonify({"ok": False, "error": "Job not cancellable"}), 409
        job["_cancel"] = True
        if status == "queued":
            job["status"] = "cancelled"
            job["log"] = "Cancelled"
        else:
            job["log"] = "Cancellation requested…"
    if status == "queued":
        persist_if_terminal(asset_id)
    else:
        kill_proc(asset_id)   # stop the running encode; the worker sets 'cancelled'
    emit_job_update(asset_id)
    broadcast_queue_update()
    return jsonify({"ok": True})


@bp.route("/api/confirm/<asset_id>", methods=["POST"])
def api_confirm(asset_id):
    """Confirm a reviewed job and replace the original on Immich (async)."""
    _env = get_env()
    with _state_lock:
        job = _jobs.get(asset_id)
        if not job:
            return jsonify({"ok": False, "error": "Unknown job"}), 404
        if job["status"] != "review":
            return jsonify({"ok": False, "error": "Job not awaiting confirmation"}), 409
        media = job.get("media")
        key_idx = job.get("_key_idx", 0)
        if media == "motionphoto":
            motion_action = job.get("motion_action", "remove")
            video_id = job.get("_video_id")
            video_name = job.get("_video_name") or job.get("name") or asset_id
            video_size = job.get("old_size") or 0
            out_path = job.get("_out_path")
            new_size = job.get("new_size")
            savings = job.get("savings")
            job["status"] = "replacing"
            job["log"] = ("Confirmed – motion video will be recompressed"
                          if motion_action == "recompress"
                          else "Confirmed – motion video will be removed")
        else:
            out_path = job.get("_out_path")
            # RAW→JPEG conversions upload under a .jpg name (set during processing).
            name = job.get("_upload_name") or job.get("name") or asset_id
            old_size = job.get("old_size")
            new_size = job.get("new_size")
            savings = job.get("savings")
            codec = job.get("codec")
            mime = "image/jpeg" if media == "image" else "video/mp4"
            job["status"] = "replacing"
            job["log"] = "Confirmed – original will be replaced"
    env = env_for_key(_env, key_idx)
    emit_job_update(asset_id)
    broadcast_queue_update()

    if media == "motionphoto":
        target, args = ((do_recompress_motion,
                         (env, asset_id, video_id, out_path, video_name,
                          video_size, new_size, savings))
                        if motion_action == "recompress"
                        else (do_strip_motion, (env, asset_id, video_id, video_name, video_size)))
        threading.Thread(target=target, args=args,
                         name=f"confirm-{asset_id}", daemon=True).start()
    else:
        threading.Thread(
            target=do_replace,
            args=(env, asset_id, name, out_path, old_size, new_size, savings, codec),
            kwargs={"mime": mime},
            name=f"confirm-{asset_id}", daemon=True,
        ).start()
    return jsonify({"ok": True})


@bp.route("/api/discard/<asset_id>", methods=["POST"])
def api_discard(asset_id):
    """Discard a reviewed/encoded result without replacing the original."""
    with _state_lock:
        job = _jobs.get(asset_id)
        if not job:
            return jsonify({"ok": False, "error": "Unknown job"}), 404
        if job["status"] not in ("review", "encoded"):
            return jsonify({"ok": False, "error": "Nothing to discard"}), 409
        out_path = job.get("_out_path")
        backup_path = job.get("_backup_path")
        job["status"] = "discarded"
        job["log"] = "Discarded – original unchanged"
        job["_out_path"] = None
        job["_backup_path"] = None
    cleanup_temp(out_path)
    if backup_path:
        cleanup_temp(backup_path)
    persist_if_terminal(asset_id)
    emit_job_update(asset_id)
    broadcast_queue_update()
    return jsonify({"ok": True})


@bp.route("/api/preview/<asset_id>")
def api_preview(asset_id):
    """Stream the encoded result of a reviewed/encoded job for in-browser playback."""
    with _state_lock:
        job = _jobs.get(asset_id)
        out_path = job.get("_out_path") if job else None
        media = job.get("media") if job else "video"
    if not out_path or not os.path.isfile(out_path):
        return Response("Preview unavailable", status=404)
    if media == "image":
        return send_file(out_path, mimetype="image/jpeg", conditional=True,
                         download_name=f"{asset_id}_preview.jpg")
    return send_file(out_path, mimetype="video/mp4", conditional=True,
                     download_name=f"{asset_id}_preview.mp4")


@bp.route("/api/clear", methods=["POST"])
def api_clear():
    removed = []
    finished = {"done", "downloaded", "encoded", "skipped",
                "error", "cancelled", "discarded"}
    with _state_lock:
        for asset_id in list(_jobs.keys()):
            if _jobs[asset_id]["status"] in finished:
                del _jobs[asset_id]
                if asset_id in _queue_order:
                    _queue_order.remove(asset_id)
                removed.append(asset_id)
    db_delete_jobs(removed)   # also drop them from the persisted history
    broadcast_queue_update()
    return jsonify({"removed": removed})


@bp.route("/api/jobs")
def api_jobs():
    with _state_lock:
        active = active_id()
        queue_ids = [aid for aid in _queue_order
                     if aid in _jobs and _jobs[aid]["status"] in
                     ("queued", "downloading", "encoding", "replacing")]
        jobs = {aid: public_job(job) for aid, job in _jobs.items()}
    return jsonify({
        "active": active,
        "queue": queue_ids,
        "stats": compute_stats(),
        "jobs": jobs,
    })


# --------------------------------------------------------------------------- #
# Routes: SSE
# --------------------------------------------------------------------------- #

@bp.route("/api/events")
def api_events():
    def stream():
        q = queue.Queue(maxsize=1000)
        with _sub_lock:
            _subscribers.append(q)
        try:
            yield f"event: connected\ndata: {json.dumps({'ts': utcnow_iso()})}\n\n"
            last_heartbeat = time.time()
            while True:
                try:
                    event, data = q.get(timeout=15)
                    yield f"event: {event}\ndata: {data}\n\n"
                except queue.Empty:
                    pass
                now = time.time()
                if now - last_heartbeat >= 15:
                    yield ": keepalive\n\n"
                    last_heartbeat = now
        finally:
            with _sub_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    return Response(stream_with_context(stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})
