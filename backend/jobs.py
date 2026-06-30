"""Job lifecycle: queue-state mutators, SSE broadcast, the encode/transcode
pipeline, the background worker and the DB persistence bridges.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time

from werkzeug.utils import secure_filename

from backend.config import (
    BACKUP_DIR, JPEG_PHOTO_EXTS, RAW_PHOTO_EXTS, TERMINAL_STATES,
    WORK_DIR, get_env, human_duration, human_size, utcnow_iso,
)
from backend.state import (
    _asset_key_lock, _asset_key_map, _jobs, _queue_order, _state_lock,
    _sub_lock, _subscribers, _work_queue, active_id, is_cancelled,
    register_proc, set_active_id, unregister_proc,
)
from backend.db import db_load_jobs, db_save_job
from backend.media import (
    append_csv_log, build_ffmpeg_image_cmd, build_handbrake_cmd, ffprobe_info,
    has_free_space,
)
from backend.immich_api import (
    asset_codec, copy_asset_metadata, copy_asset_tags, download_original,
    env_for_asset_verified, env_for_key, fetch_asset, get_asset_meta,
    get_asset_tag_ids, immich_headers, set_live_photo_link, trash_asset,
    update_asset, upload_new_asset,
)

def broadcast(event, data):
    """Send an SSE event to all connected subscribers."""
    payload = (event, json.dumps(data))
    with _sub_lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


def broadcast_queue_update():
    broadcast("queue_update", {"ts": utcnow_iso()})




def new_job(asset_id, params):
    return {
        "id": asset_id,
        "name": params.get("name", asset_id),
        "size": params.get("size"),
        "status": "queued",
        "progress": 0.0,
        "log": "",
        "codec": None,
        "new_codec": None,
        "new_id": None,
        "old_size": None,
        "new_size": None,
        "savings": None,
        "savings_human": None,
        "confirm": params.get("confirm", True),
        "media": params.get("media", "video"),
        "encoder": params.get("encoder", "x265"),
        "quality": params.get("quality", 24),
        "photo_target_savings": params.get("photo_target_savings", 40),
        "compress_raw": params.get("compress_raw", False),
        "preset": params.get("preset", "medium"),
        "threads": params.get("threads"),
        "resolution": params.get("resolution", "original"),
        "motion_action": params.get("motion_action", "remove"),
        "skip_codecs": params.get("skip_codecs", "hevc,av1"),
        "min_savings": params.get("min_savings", 10),
        "replace": params.get("replace", True),
        "download_only": params.get("download_only", False),
        "backup_dir": params.get("backup_dir") or BACKUP_DIR,
        "started_at": None,
        "_cancel": False,
        "_out_path": None,
        "_backup_path": None,
        "_upload_name": None,
        "_video_id": None,
        "_video_name": None,
    }


def public_job(job):
    """Return a job dict without private fields for API responses."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def update_job(asset_id, **changes):
    with _state_lock:
        job = _jobs.get(asset_id)
        if not job:
            return None
        job.update(changes)
        snapshot = public_job(job)
    return snapshot


def emit_job_update(asset_id):
    with _state_lock:
        job = _jobs.get(asset_id)
        if not job:
            return
        data = {
            "id": asset_id,
            "status": job["status"],
            "progress": job["progress"],
            "log": job["log"],
            "new_size": job["new_size"],
            "savings": job["savings"],
        }
    broadcast("job_update", data)


def compute_stats():
    stats = {"queued": 0, "active": 0, "review": 0, "done": 0,
             "skipped": 0, "error": 0, "saved_bytes": 0}
    with _state_lock:
        for job in _jobs.values():
            status = job["status"]
            if status == "queued":
                stats["queued"] += 1
            elif status in ("encoding", "downloading", "replacing"):
                stats["active"] += 1
            elif status == "review":
                stats["review"] += 1
            elif status in ("done", "downloaded", "encoded"):
                stats["done"] += 1
            elif status == "skipped":
                stats["skipped"] += 1
            elif status == "error":
                stats["error"] += 1
            # Only count savings for assets that were actually replaced on the
            # server. Skipped (low savings) and encoded-but-not-replaced jobs
            # carry a savings figure but did not change anything upstream.
            if status == "done" and job.get("savings"):
                try:
                    stats["saved_bytes"] += int(job["savings"])
                except (TypeError, ValueError):
                    pass
    return stats




def persist_if_terminal(asset_id):
    """Write a job to the DB once it reaches a terminal (finished) state."""
    with _state_lock:
        job = _jobs.get(asset_id)
        snap = dict(job) if job and job.get("status") in TERMINAL_STATES else None
    if snap:
        db_save_job(snap)


def load_persisted_jobs():
    """Restore finished jobs from the DB into memory on startup."""
    for r in db_load_jobs():
        aid = r.get("id")
        if not aid or r.get("status") not in TERMINAL_STATES:
            continue
        job = new_job(aid, {"name": r.get("name") or aid,
                            "media": r.get("media") or "video"})
        for k in ("status", "old_size", "new_size", "savings",
                  "codec", "new_codec", "log"):
            job[k] = r.get(k)
        if job.get("new_size") is not None and job.get("savings") is not None:
            job["savings_human"] = human_size(job["savings"])
        with _state_lock:
            _jobs[aid] = job
            if aid not in _queue_order:
                _queue_order.append(aid)




_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _parse_pct(line):
    """Extract a progress percentage (0–100 float) from a HandBrake output line.

    Reads the number attached to the ``%`` sign rather than the first number in
    the line: HandBrake's progress line is ``Encoding: task 1 of 1, 47.50 %``,
    so a naive "first number 0–100" scan would lock onto the ``1`` in "task 1 of
    1" and pin progress at 1%.
    """
    m = _PCT_RE.search(line)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return val if 0.0 <= val <= 100.0 else None


def _progress_from_line(line):
    """Progress % for the bar, or None. Only HandBrake's *encode* phase counts.

    HandBrake's preview **scan** also runs 0→100% (``Scanning title 1 of 1,
    preview 10, 100.00 %``) and finishes before the encode starts, so counting it
    would slam the bar to 100% while the real work hasn't begun. Muxing/optimize
    and activity-log lines carry no encode percentage either.
    """
    if not line.lstrip().startswith("Encoding"):
        return None
    return _parse_pct(line)


def run_handbrake(cmd, asset_id, source_duration):
    """Run HandBrakeCLI, parsing progress from its output. Returns True on success.

    HandBrakeCLI writes the live ``Encoding: task 1 of 1, X %`` progress to
    *stdout*, and its activity log (plus any error detail) to *stderr*, so we
    merge stderr into stdout and read the combined stream — discarding stdout
    (as before) would mean never seeing real encode progress. Progress lines use
    \\r (carriage return), not \\n, so we read char-by-char and split on both.
    """
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=0)
    except (OSError, ValueError):
        update_job(asset_id, log="Failed to start HandBrakeCLI")
        emit_job_update(asset_id)
        return False
    register_proc(asset_id, proc)   # so a running encode can be killed/cancelled

    last_emit = 0.0
    buf = ""
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        if ch in ("\r", "\n"):
            line = buf.strip()
            buf = ""
            if not line:
                continue
            pct = _progress_from_line(line)
            changes = {"log": line}
            if pct is not None:
                changes["progress"] = round(pct, 1)
            update_job(asset_id, **changes)
            now = time.time()
            if now - last_emit > 0.5:
                emit_job_update(asset_id)
                last_emit = now
        else:
            buf += ch

    # flush any remaining buffer (no trailing newline)
    if buf.strip():
        line = buf.strip()
        pct = _progress_from_line(line)
        changes = {"log": line}
        if pct is not None:
            changes["progress"] = round(pct, 1)
        update_job(asset_id, **changes)

    proc.wait()
    unregister_proc(asset_id)
    emit_job_update(asset_id)
    return proc.returncode == 0


def cleanup_temp(*paths):
    for p in paths:
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _replace_error(asset_id, name, codec, old_size, new_size, savings, log, out_path=None):
    update_job(asset_id, status="error", log=log)
    emit_job_update(asset_id)
    append_csv_log([utcnow_iso(), asset_id, name, codec or "",
                    old_size, new_size, savings, "error"])
    cleanup_temp(out_path)
    persist_if_terminal(asset_id)
    broadcast_queue_update()
    return False


def do_replace(env, asset_id, name, out_path, old_size, new_size, savings, codec,
               mime="video/mp4"):
    """Replace an asset using Immich's current workflow (replaceAsset is deprecated):

      1. upload the compressed file as a NEW asset,
      2. copy metadata (albums / favorite / shared links / stack / sidecar) from
         the old asset to it via copyAsset,
      3. move the OLD asset to the trash (recoverable for the retention period).

    The original's capture date is set on the new asset so it keeps its place in
    the timeline. Returns True on success. Used by the worker and the confirm
    endpoint.
    """
    update_job(asset_id, status="replacing", log="Uploading compressed file…")
    emit_job_update(asset_id)
    if not out_path or not os.path.isfile(out_path):
        return _replace_error(asset_id, name, codec, old_size, new_size, savings,
                              "Encoded file missing")

    meta = get_asset_meta(env, asset_id)
    new_id, status = upload_new_asset(env, out_path, name,
                                      meta.get("fileCreatedAt"),
                                      meta.get("fileModifiedAt"), mime)
    if not new_id:
        return _replace_error(asset_id, name, codec, old_size, new_size, savings,
                              f"Upload failed: {status}", out_path)
    if status not in ("created", None):
        # 'duplicate' → Immich already has this file; don't trash the original.
        return _replace_error(asset_id, name, codec, old_size, new_size, savings,
                              f"Upload detected as '{status}' – original NOT deleted", out_path)

    # Remember the new asset id so the UI can link to it (the original is trashed).
    update_job(asset_id, new_id=new_id)

    update_job(asset_id, log="Copying metadata (albums, favorite, …)…")
    emit_job_update(asset_id)
    if not copy_asset_metadata(env, asset_id, new_id):
        # New asset exists but metadata copy failed — leave the original intact.
        return _replace_error(asset_id, name, codec, old_size, new_size, savings,
                              "Metadata copy failed – new asset created, original kept", out_path)

    update_job(asset_id, log="Moving original to trash…")
    emit_job_update(asset_id)
    if not trash_asset(env, asset_id):
        return _replace_error(asset_id, name, codec, old_size, new_size, savings,
                              "Could not move original to trash", out_path)

    update_job(asset_id, status="done",
               log="Done – new asset created, original in trash")
    emit_job_update(asset_id)
    append_csv_log([utcnow_iso(), asset_id, name, codec or "",
                    old_size, new_size, savings, "done"])
    cleanup_temp(out_path)
    persist_if_terminal(asset_id)
    broadcast_queue_update()
    return True


def do_strip_motion(env, image_id, video_id, name, video_size):
    """Strip a Live/motion photo's video: clear the still image's
    livePhotoVideoId, then trash the (hidden) motion-video asset. The still
    photo itself is untouched. Shared by the worker and the confirm endpoint.
    Returns True on success."""
    update_job(image_id, status="replacing", log="Removing motion link…")
    emit_job_update(image_id)
    if not video_id:
        return _replace_error(image_id, name, "MOTION", video_size, video_size, 0,
                              "No motion video linked")
    if not set_live_photo_link(env, image_id, None):
        return _replace_error(image_id, name, "MOTION", video_size, video_size, 0,
                              "Could not remove motion link")
    update_job(image_id, log="Moving motion video to trash…")
    emit_job_update(image_id)
    if not trash_asset(env, video_id):
        return _replace_error(image_id, name, "MOTION", video_size, video_size, 0,
                              "Link removed, but video could not be deleted")
    update_job(image_id, status="done", new_size=0, savings=video_size,
               savings_human=human_size(video_size),
               log="Done – motion video removed, photo unchanged")
    emit_job_update(image_id)
    append_csv_log([utcnow_iso(), image_id, name, "MOTION",
                    video_size, 0, video_size, "done"])
    persist_if_terminal(image_id)
    broadcast_queue_update()
    return True


def do_recompress_motion(env, image_id, old_video_id, out_path, video_name,
                         old_size, new_size, savings):
    """Replace a Live/motion photo's video with a recompressed one: upload the
    new clip as a hidden asset, repoint the still's livePhotoVideoId to it, then
    trash the old video. The still photo is untouched. Returns True on success."""
    update_job(image_id, status="replacing", log="Uploading compressed video…")
    emit_job_update(image_id)
    if not out_path or not os.path.isfile(out_path):
        return _replace_error(image_id, video_name, "MOTION", old_size, new_size, savings,
                              "Compressed video missing", out_path)

    meta = get_asset_meta(env, old_video_id)
    new_id, status = upload_new_asset(env, out_path, video_name,
                                      meta.get("fileCreatedAt"),
                                      meta.get("fileModifiedAt"), "video/mp4")
    if not new_id:
        return _replace_error(image_id, video_name, "MOTION", old_size, new_size, savings,
                              f"Upload failed: {status}", out_path)
    if status not in ("created", None):
        return _replace_error(image_id, video_name, "MOTION", old_size, new_size, savings,
                              f"Upload detected as '{status}' – nothing changed", out_path)

    # Keep the motion video off the main timeline.
    update_asset(env, new_id, visibility="hidden")

    update_job(image_id, log="Linking new motion video…")
    emit_job_update(image_id)
    if not set_live_photo_link(env, image_id, new_id):
        return _replace_error(image_id, video_name, "MOTION", old_size, new_size, savings,
                              "Could not link new video", out_path)

    update_job(image_id, log="Moving old video to trash…")
    emit_job_update(image_id)
    if not trash_asset(env, old_video_id):
        return _replace_error(image_id, video_name, "MOTION", old_size, new_size, savings,
                              "Linked, but old video could not be deleted", out_path)

    update_job(image_id, status="done", new_size=new_size, savings=savings,
               savings_human=human_size(savings),
               log="Done – motion video recompressed, photo unchanged")
    emit_job_update(image_id)
    append_csv_log([utcnow_iso(), image_id, video_name, "MOTION",
                    old_size, new_size, savings, "done"])
    cleanup_temp(out_path)
    persist_if_terminal(image_id)
    broadcast_queue_update()
    return True


def _read_jpeg_exif_segment(path):
    """Return the raw APP1 'Exif' segment (marker+length+payload) of a JPEG, or
    None. ffmpeg strips EXIF on re-encode, so we transplant it back afterwards
    to keep capture date / GPS / camera / orientation."""
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"\xFF\xD8":          # SOI
                return None
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                m = marker[1]
                if m == 0xDA or m == 0xD9:          # SOS / EOI -> no more headers
                    return None
                length_bytes = fh.read(2)
                if len(length_bytes) < 2:
                    return None
                seg_len = int.from_bytes(length_bytes, "big")
                payload = fh.read(seg_len - 2)
                if len(payload) < seg_len - 2:
                    return None
                if m == 0xE1 and payload[:6] == b"Exif\x00\x00":
                    return marker + length_bytes + payload
    except OSError:
        return None


def _inject_jpeg_exif(path, exif_segment):
    """Insert an APP1 'Exif' segment right after the SOI marker of a JPEG."""
    if not exif_segment:
        return False
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if data[:2] != b"\xFF\xD8":
            return False
        with open(path, "wb") as fh:
            fh.write(data[:2] + exif_segment + data[2:])
        return True
    except OSError:
        return False


def _run_killable(cmd, asset_id, timeout=300):
    """Run a command via Popen, registered so cancel can kill it.
    Returns (returncode, combined_output)."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    except (OSError, ValueError) as exc:
        return 1, str(exc)
    register_proc(asset_id, proc)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        out = (out or "") + "\nTimeout"
    finally:
        unregister_proc(asset_id)
    return proc.returncode, out or ""


def _encode_ffmpeg_image(src, out, level, asset_id):
    """Encode JPEG with ffmpeg at -q:v `level` (2..31, higher = smaller).
    Returns (ok, err_text)."""
    rc, output = _run_killable(build_ffmpeg_image_cmd(src, out, level), asset_id)
    if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
        tail = [ln for ln in output.splitlines() if ln.strip()]
        return False, (tail[-1] if tail else "ffmpeg failed")
    return True, ""


def _encode_sips_image(src, out, level, asset_id):
    """Encode JPEG with macOS `sips` at quality `level` (0..100, lower = smaller).
    Uses Apple's ImageIO, which reads JPEGs ffmpeg's mjpeg decoder rejects
    (e.g. Samsung panoramas) and preserves EXIF. Returns (ok, err_text)."""
    rc, output = _run_killable(
        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(level),
         src, "--out", out], asset_id)
    if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
        tail = [ln for ln in output.splitlines() if ln.strip()]
        return False, (tail[-1] if tail else "sips failed")
    return True, ""


# Quality levels ordered so that the encoded file gets *smaller* along the list
# (least compression first). Used for the binary search below.
_FFMPEG_LEVELS = list(range(2, 32))           # -q:v 2..31
_SIPS_LEVELS = list(range(98, 4, -3))         # sips quality 98,95,92,…,5


def _search_target_level(encode, levels, probe, target_size, asset_id, label, max_it=6):
    """Binary-search `levels` for the least-aggressive quality whose encoded size
    is <= target_size. Returns (index, "") or (None, err_text) if `encode` fails."""
    lo, hi = 0, len(levels) - 1
    best = None
    it = 0
    while lo <= hi and it < max_it:
        if is_cancelled(asset_id):
            return None, "Cancelled"
        mid = (lo + hi) // 2
        update_job(asset_id, progress=round(15 + 70 * it / max_it, 1),
                   log=f"Searching quality ({label})…")
        emit_job_update(asset_id)
        ok, err = encode(levels[mid], probe)
        if not ok:
            return None, err
        size = os.path.getsize(probe)
        if size <= target_size:
            best = mid            # meets target; try less compression (earlier idx)
            hi = mid - 1
        else:
            lo = mid + 1          # not enough savings; compress harder
        it += 1
    return (best if best is not None else len(levels) - 1), ""


def compress_image_to_target(src, out, old_size, target_pct, asset_id):
    """Compress a JPEG so its size shrinks by roughly `target_pct` percent.

    Tries ffmpeg first (cross-platform); if ffmpeg can't decode the file, falls
    back to macOS `sips`. Binary-searches the quality for the least aggressive
    setting that still reaches the requested savings.

    Returns (ok, used_quality_label, achieved_pct).
    """
    have_ffmpeg = bool(shutil.which("ffmpeg"))
    have_sips = bool(shutil.which("sips"))
    if not have_ffmpeg and not have_sips:
        update_job(asset_id, log="Neither ffmpeg nor sips found – photo compression not possible")
        emit_job_update(asset_id)
        return False, None, 0.0

    try:
        target_pct = max(0.0, min(95.0, float(target_pct)))
    except (TypeError, ValueError):
        target_pct = 40.0
    target_size = old_size * (1.0 - target_pct / 100.0)

    exif_segment = _read_jpeg_exif_segment(src)   # ffmpeg drops EXIF; re-add later

    probe = out + ".probe.jpg"

    # Encoder attempt order: ffmpeg (fast, portable) then sips (macOS fallback).
    attempts = []
    if have_ffmpeg:
        attempts.append(("ffmpeg", _FFMPEG_LEVELS,
                         lambda lvl, dst: _encode_ffmpeg_image(src, dst, lvl, asset_id), True))
    if have_sips:
        attempts.append(("sips", _SIPS_LEVELS,
                         lambda lvl, dst: _encode_sips_image(src, dst, lvl, asset_id), False))

    last_err = "Compression failed"
    for name, levels, encode, needs_exif in attempts:
        idx, err = _search_target_level(encode, levels, probe, target_size,
                                        asset_id, name)
        if idx is None:
            last_err = err           # this encoder failed; try the next one
            if is_cancelled(asset_id):
                break                # don't fall through to the next encoder
            continue
        update_job(asset_id, progress=92.0, log=f"Finale Kompression ({name})")
        emit_job_update(asset_id)
        ok, err = encode(levels[idx], out)
        if not ok:
            last_err = err
            continue
        if needs_exif and exif_segment:
            _inject_jpeg_exif(out, exif_segment)
        cleanup_temp(probe)
        new_size = os.path.getsize(out)
        achieved = (1.0 - new_size / old_size) * 100.0 if old_size else 0.0
        update_job(asset_id, progress=100.0)
        emit_job_update(asset_id)
        return True, f"{name} q{levels[idx]}", achieved

    cleanup_temp(probe)
    update_job(asset_id, log="Kompression: " + last_err)
    emit_job_update(asset_id)
    return False, None, 0.0


def process_image_job(env, asset_id, params):
    """Download, recompress (JPEG) and optionally replace an image asset."""
    name = params.get("name") or asset_id
    download_only = params.get("download_only", False)
    replace = params.get("replace", True)
    confirm = params.get("confirm", True)
    backup_dir = params.get("backup_dir") or BACKUP_DIR
    target_savings = params.get("photo_target_savings", 40)
    compress_raw = params.get("compress_raw", False)

    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(backup_dir, exist_ok=True)

    ext = os.path.splitext(name)[1] or ".jpg"
    src_fmt = (os.path.splitext(name)[1].lstrip(".") or "img").upper()
    src_path = os.path.join(WORK_DIR, f"{asset_id}_src{ext}")
    out_path = os.path.join(WORK_DIR, f"{asset_id}_out.jpg")

    fmt = src_fmt.lower()
    is_raw = fmt in RAW_PHOTO_EXTS
    # RAW conversions upload as .jpg; JPEG keeps its original filename.
    upload_name = (os.path.splitext(name)[0] + ".jpg") if is_raw else name

    # JPEG is recompressed in place (no format change). RAW can optionally be
    # converted to JPEG (opt-in) — that needs sips and *replaces* the RAW. Every
    # other format is skipped so nothing is silently converted.
    if not download_only:
        skip_msg = None
        if is_raw and not compress_raw:
            skip_msg = f"{src_fmt}: RAW is not compressed (enable it in settings)"
        elif is_raw and not shutil.which("sips"):
            skip_msg = f"{src_fmt}: RAW compression requires 'sips' (macOS only)"
        elif not is_raw and fmt not in JPEG_PHOTO_EXTS:
            skip_msg = f"{src_fmt}: only JPEG is compressed (no format conversion)"
        if skip_msg:
            update_job(asset_id, status="skipped", codec=src_fmt, log=skip_msg)
            emit_job_update(asset_id)
            append_csv_log([utcnow_iso(), asset_id, name, src_fmt,
                            None, None, None, "skipped"])
            broadcast_queue_update()
            return

    job_size = params.get("size") or 0
    ok_space, need = has_free_space(job_size)
    if job_size and not ok_space:
        free = shutil.disk_usage(WORK_DIR).free
        update_job(asset_id, status="error",
                   log=f"Not enough disk space: {human_size(free)} free, ~{human_size(need)} needed")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_fmt, None, None, None, "error"])
        broadcast_queue_update()
        return

    update_job(asset_id, status="downloading", started_at=utcnow_iso(),
               codec=src_fmt, log="Downloading original")
    emit_job_update(asset_id)

    ok, reason = download_original(env, asset_id, src_path)
    if not ok:
        cancelled = reason == "cancelled"
        update_job(asset_id, status="cancelled" if cancelled else "error",
                   log="Cancelled" if cancelled else f"Download failed: {reason}")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, "", None, None, None,
                        "cancelled" if cancelled else "error"])
        cleanup_temp(src_path)
        return

    old_size = os.path.getsize(src_path) if os.path.isfile(src_path) else 0
    update_job(asset_id, old_size=old_size)

    if download_only:
        update_job(asset_id, status="downloaded", progress=100.0,
                   log=f"Downloaded to {src_path}")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_fmt,
                        old_size, None, None, "downloaded"])
        return

    update_job(asset_id, status="encoding", progress=0.0,
               log=f"Compressing photo (target ~{float(target_savings):.0f}%)")
    emit_job_update(asset_id)

    ok, used_q, achieved = compress_image_to_target(
        src_path, out_path, old_size, target_savings, asset_id)
    if not ok or not os.path.isfile(out_path):
        cancelled = is_cancelled(asset_id)
        # keep the detailed ffmpeg message unless the user cancelled
        update_job(asset_id, status="cancelled" if cancelled else "error",
                   **({"log": "Cancelled"} if cancelled else {}))
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_fmt, old_size, None,
                        None, "cancelled" if cancelled else "error"])
        cleanup_temp(src_path, out_path)
        return

    new_size = os.path.getsize(out_path)
    savings = old_size - new_size
    savings_pct = (savings / old_size * 100.0) if old_size else 0.0
    min_savings = params.get("min_savings", 10)

    if savings_pct < float(min_savings):
        update_job(asset_id, status="skipped", new_size=new_size,
                   savings=max(savings, 0), savings_human=human_size(max(savings, 0)),
                   log=f"Savings {savings_pct:.1f}% < {min_savings}%")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_fmt,
                        old_size, new_size, savings, "skipped"])
        cleanup_temp(src_path, out_path)
        return

    update_job(asset_id, status="encoded", progress=100.0, new_size=new_size,
               savings=savings, savings_human=human_size(savings),
               new_codec="JPEG",
               log=f"Compressed: {savings_pct:.0f}% saved (target ~{float(target_savings):.0f}%, {used_q})")
    emit_job_update(asset_id)

    if not replace:
        update_job(asset_id, status="encoded", _out_path=out_path,
                   log=f"Compressed, saved to {out_path}")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_fmt,
                        old_size, new_size, savings, "encoded"])
        cleanup_temp(src_path)
        return

    # secure_filename keeps the original name readable while stripping path
    # separators and ".." so a crafted asset name cannot escape backup_dir.
    backup_path = os.path.join(backup_dir, secure_filename(f"{asset_id}_{name}"))
    try:
        shutil.copy2(src_path, backup_path)
    except OSError:
        backup_path = None
    cleanup_temp(src_path)

    if confirm:
        update_job(asset_id, status="review", _out_path=out_path,
                   _backup_path=backup_path, _upload_name=upload_name,
                   log="Ready for confirmation – review the result and approve")
        emit_job_update(asset_id)
        return

    do_replace(env, asset_id, upload_name, out_path, old_size, new_size,
               savings, src_fmt, mime="image/jpeg")


def process_motionphoto_job(env, asset_id, params):
    """Act on a Live/motion photo's video. The job's asset_id is the still IMAGE;
    the motion video is its linked (hidden) VIDEO asset. The action is set by
    `motion_action`: 'remove' (strip), 'recompress' (re-encode + relink), or
    'keep' (analyze only). The still photo is never modified."""
    name = params.get("name") or asset_id
    motion_action = params.get("motion_action", "remove")
    download_only = params.get("download_only", False)
    replace = params.get("replace", True)
    confirm = params.get("confirm", True)
    backup_dir = params.get("backup_dir") or BACKUP_DIR
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(backup_dir, exist_ok=True)

    update_job(asset_id, status="downloading", started_at=utcnow_iso(),
               codec="MOTION", log="Searching for motion video…")
    emit_job_update(asset_id)

    detail = fetch_asset(env, asset_id)
    video_id = detail.get("livePhotoVideoId")
    if not video_id:
        update_job(asset_id, status="skipped",
                   log="No motion video (livePhotoVideoId empty)")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, "MOTION", None, None, None, "skipped"])
        broadcast_queue_update()
        return

    vdetail = fetch_asset(env, video_id)
    vinfo = vdetail.get("exifInfo") or {}
    try:
        video_size = int(vinfo.get("fileSizeInByte") or vdetail.get("fileSizeInByte") or 0)
    except (TypeError, ValueError):
        video_size = 0
    video_name = vdetail.get("originalFileName") or f"{video_id}.mov"
    update_job(asset_id, old_size=video_size)

    if download_only:
        ext = os.path.splitext(video_name)[1] or ".mov"
        dest = os.path.join(WORK_DIR, f"{video_id}_motion{ext}")
        ok, reason = download_original(env, video_id, dest)
        if not ok:
            update_job(asset_id, status="error",
                       log=f"Motion video download failed: {reason}")
            emit_job_update(asset_id)
            append_csv_log([utcnow_iso(), asset_id, name, "MOTION", video_size, None, None, "error"])
            broadcast_queue_update()
            return
        update_job(asset_id, status="downloaded", progress=100.0,
                   log=f"Motion video downloaded: {dest}")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, "MOTION", video_size, None, None, "downloaded"])
        return

    # 'keep' → report only, never touch the asset.
    if motion_action == "keep":
        update_job(asset_id, status="skipped", new_size=video_size, savings=0,
                   log=f"Live Photo kept – motion video ({human_size(video_size)}) unchanged")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, "MOTION", video_size, video_size, 0, "skipped"])
        broadcast_queue_update()
        return

    if motion_action == "recompress":
        vext = os.path.splitext(video_name)[1] or ".mov"
        src_path = os.path.join(WORK_DIR, f"{video_id}_src{vext}")
        out_path = os.path.join(WORK_DIR, f"{asset_id}_out.mp4")
        update_job(asset_id, log="Downloading motion video…")
        emit_job_update(asset_id)
        ok, reason = download_original(env, video_id, src_path)
        if not ok:
            cancelled = reason == "cancelled"
            update_job(asset_id, status="cancelled" if cancelled else "error",
                       log="Cancelled" if cancelled else f"Motion video download failed: {reason}")
            emit_job_update(asset_id)
            append_csv_log([utcnow_iso(), asset_id, name, "MOTION", video_size, None, None,
                            "cancelled" if cancelled else "error"])
            cleanup_temp(src_path)
            return

        backup_path = os.path.join(backup_dir, f"{video_id}_{video_name}")
        try:
            shutil.copy2(src_path, backup_path)
        except OSError:
            backup_path = None

        _, src_duration = ffprobe_info(src_path)
        update_job(asset_id, status="encoding", progress=0.0, log="Compressing motion video")
        emit_job_update(asset_id)
        cmd = build_handbrake_cmd(src_path, out_path, params.get("encoder", "x265"),
                                  params.get("quality", 24), params.get("preset", "medium"),
                                  params.get("resolution", "original"), params.get("threads"))
        ok = run_handbrake(cmd, asset_id, src_duration)
        if not ok or not os.path.isfile(out_path):
            cancelled = is_cancelled(asset_id)
            update_job(asset_id, status="cancelled" if cancelled else "error",
                       log="Cancelled" if cancelled else "HandBrake error")
            emit_job_update(asset_id)
            append_csv_log([utcnow_iso(), asset_id, name, "MOTION", video_size, None, None,
                            "cancelled" if cancelled else "error"])
            cleanup_temp(src_path, out_path)
            return

        new_size = os.path.getsize(out_path)
        savings = video_size - new_size
        savings_pct = (savings / video_size * 100.0) if video_size else 0.0
        min_savings = params.get("min_savings", 10)
        cleanup_temp(src_path)
        if savings_pct < float(min_savings):
            update_job(asset_id, status="skipped", new_size=new_size,
                       savings=max(savings, 0), savings_human=human_size(max(savings, 0)),
                       log=f"Savings {savings_pct:.1f}% < {min_savings}%")
            emit_job_update(asset_id)
            append_csv_log([utcnow_iso(), asset_id, name, "MOTION", video_size, new_size, savings, "skipped"])
            cleanup_temp(out_path)
            return

        update_job(asset_id, status="encoded", progress=100.0, new_size=new_size,
                   savings=savings, savings_human=human_size(savings), new_codec="HEVC",
                   log=f"Compressed: {savings_pct:.0f}% saved")
        emit_job_update(asset_id)

        if not replace:
            update_job(asset_id, status="encoded", _out_path=out_path,
                       log=f"Compressed, saved to {out_path}")
            emit_job_update(asset_id)
            append_csv_log([utcnow_iso(), asset_id, name, "MOTION", video_size, new_size, savings, "encoded"])
            return

        if confirm:
            update_job(asset_id, status="review", _out_path=out_path, _video_id=video_id,
                       _video_name=video_name, _backup_path=backup_path,
                       log="Ready for confirmation – review the result and approve")
            emit_job_update(asset_id)
            return

        do_recompress_motion(env, asset_id, video_id, out_path, video_name,
                             video_size, new_size, savings)
        return

    # 'remove' (strip) — back up the motion video locally, then unlink + trash it.
    backup_path = None
    if replace:
        backup_path = os.path.join(backup_dir, f"{video_id}_{video_name}")
        ok, _ = download_original(env, video_id, backup_path)
        if not ok:
            backup_path = None

    update_job(asset_id, status="encoded", progress=100.0, new_size=0,
               savings=video_size, savings_human=human_size(video_size),
               new_codec="—",
               log=f"Motion video found ({human_size(video_size)})")
    emit_job_update(asset_id)

    if not replace:
        update_job(asset_id, status="encoded",
                   log="Analyzed – motion video not removed")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, "MOTION", video_size, 0, video_size, "encoded"])
        return

    if confirm:
        update_job(asset_id, status="review", _video_id=video_id,
                   _video_name=video_name, _backup_path=backup_path,
                   log="Ready – motion video will be removed (photo is preserved)")
        emit_job_update(asset_id)
        return

    do_strip_motion(env, asset_id, video_id, name, video_size)


def process_job(asset_id):
    _env = get_env()
    with _state_lock:
        job = _jobs.get(asset_id)
        if not job:
            return
        if job.get("_cancel"):
            job["status"] = "cancelled"
        params = dict(job)
    if params.get("_cancel"):
        emit_job_update(asset_id)
        return

    # Use the key that actually owns this asset, not just the one captured at
    # enqueue (which can be wrong/stale in multi-user setups → Immich 400).
    env = env_for_asset_verified(_env, asset_id, params.get("_key_idx", 0))
    with _asset_key_lock:
        resolved_idx = _asset_key_map.get(asset_id, params.get("_key_idx", 0))
    # Persist the corrected key on the job so the confirm/replace path uses it too.
    params["_key_idx"] = resolved_idx
    update_job(asset_id, _key_idx=resolved_idx)

    if params.get("media") == "image":
        process_image_job(env, asset_id, params)
        return

    if params.get("media") == "motionphoto":
        process_motionphoto_job(env, asset_id, params)
        return

    name = params.get("name") or asset_id
    skip_codecs = [c.strip().lower() for c in str(params.get("skip_codecs", "")).split(",") if c.strip()]
    download_only = params.get("download_only", False)
    replace = params.get("replace", True)
    backup_dir = params.get("backup_dir") or BACKUP_DIR

    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(backup_dir, exist_ok=True)

    ext = os.path.splitext(name)[1] or ".bin"
    src_path = os.path.join(WORK_DIR, f"{asset_id}_src{ext}")
    out_path = os.path.join(WORK_DIR, f"{asset_id}_out.mp4")

    job_size = params.get("size") or 0
    ok_space, need = has_free_space(job_size)
    if job_size and not ok_space:
        free = shutil.disk_usage(WORK_DIR).free
        update_job(asset_id, status="error",
                   log=f"Not enough disk space: {human_size(free)} free, ~{human_size(need)} needed")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, "", None, None, None, "error"])
        broadcast_queue_update()
        return

    update_job(asset_id, status="downloading", started_at=utcnow_iso(),
               log="Downloading original")
    emit_job_update(asset_id)

    ok, reason = download_original(env, asset_id, src_path)
    if not ok:
        cancelled = reason == "cancelled"
        update_job(asset_id, status="cancelled" if cancelled else "error",
                   log="Cancelled" if cancelled else f"Download failed: {reason}")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, "", None, None, None,
                        "cancelled" if cancelled else "error"])
        cleanup_temp(src_path)
        return

    old_size = os.path.getsize(src_path) if os.path.isfile(src_path) else 0
    src_codec, src_duration = ffprobe_info(src_path)
    update_job(asset_id, old_size=old_size, codec=src_codec)

    if download_only:
        update_job(asset_id, status="downloaded", progress=100.0,
                   log=f"Downloaded to {src_path}")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_codec or "",
                        old_size, None, None, "downloaded"])
        return

    if src_codec and src_codec.lower() in skip_codecs:
        update_job(asset_id, status="skipped", log=f"Codec {src_codec} in skip list")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_codec,
                        old_size, None, None, "skipped"])
        cleanup_temp(src_path)
        return

    update_job(asset_id, status="encoding", progress=0.0, log="Encoding")
    emit_job_update(asset_id)

    cmd = build_handbrake_cmd(src_path, out_path, params.get("encoder", "x265"),
                              params.get("quality", 24), params.get("preset", "medium"),
                              params.get("resolution", "original"), params.get("threads"))
    ok = run_handbrake(cmd, asset_id, src_duration)
    if not ok or not os.path.isfile(out_path):
        cancelled = is_cancelled(asset_id)
        update_job(asset_id, status="cancelled" if cancelled else "error",
                   log="Cancelled" if cancelled else "HandBrake error")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_codec or "",
                        old_size, None, None, "cancelled" if cancelled else "error"])
        cleanup_temp(src_path, out_path)
        return

    out_codec, out_duration = ffprobe_info(out_path)
    if (src_duration is not None and out_duration is not None
            and abs(src_duration - out_duration) > 2.0):
        update_job(asset_id, status="error",
                   log=f"Duration deviation {abs(src_duration - out_duration):.1f}s")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_codec or "",
                        old_size, None, None, "error"])
        cleanup_temp(src_path, out_path)
        return

    new_size = os.path.getsize(out_path)
    savings = old_size - new_size
    savings_pct = (savings / old_size * 100.0) if old_size else 0.0
    min_savings = params.get("min_savings", 10)

    if savings_pct < float(min_savings):
        update_job(asset_id, status="skipped", new_size=new_size,
                   savings=max(savings, 0), savings_human=human_size(max(savings, 0)),
                   log=f"Savings {savings_pct:.1f}% < {min_savings}%")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_codec or "",
                        old_size, new_size, savings, "skipped"])
        cleanup_temp(src_path, out_path)
        return

    confirm = params.get("confirm", True)

    update_job(asset_id, status="encoded", progress=100.0, new_size=new_size,
               savings=savings, savings_human=human_size(savings),
               new_codec=out_codec, log="Encoded")
    emit_job_update(asset_id)

    if not replace:
        update_job(asset_id, status="encoded", _out_path=out_path,
                   log=f"Encoded, kept at {out_path}")
        emit_job_update(asset_id)
        append_csv_log([utcnow_iso(), asset_id, name, src_codec or "",
                        old_size, new_size, savings, "encoded"])
        cleanup_temp(src_path)
        return

    # secure_filename keeps the original name readable while stripping path
    # separators and ".." so a crafted asset name cannot escape backup_dir.
    backup_path = os.path.join(backup_dir, secure_filename(f"{asset_id}_{name}"))
    try:
        shutil.copy2(src_path, backup_path)
    except OSError:
        backup_path = None
    cleanup_temp(src_path)

    if confirm:
        update_job(asset_id, status="review", _out_path=out_path,
                   _backup_path=backup_path,
                   log="Ready for confirmation – review the result and approve")
        emit_job_update(asset_id)
        return

    do_replace(env, asset_id, name, out_path, old_size, new_size,
               savings, src_codec)


def worker_loop():
    """Background worker: process queued jobs one at a time."""
    while True:
        asset_id = _work_queue.get()
        try:
            with _state_lock:
                job = _jobs.get(asset_id)
                if not job or job.get("_cancel") or job["status"] != "queued":
                    if job and job.get("_cancel"):
                        job["status"] = "cancelled"
                    set_active_id(None)
                else:
                    set_active_id(asset_id)
            if active_id() != asset_id:
                broadcast_queue_update()
                continue
            broadcast_queue_update()
            process_job(asset_id)
        except Exception as exc:  # keep the worker alive on unexpected errors
            update_job(asset_id, status="error", log=f"Worker error: {exc}")
            emit_job_update(asset_id)
        finally:
            with _state_lock:
                set_active_id(None)
            unregister_proc(asset_id)
            persist_if_terminal(asset_id)   # save finished jobs to the DB
            broadcast_queue_update()
            _work_queue.task_done()




def start_worker():
    thread = threading.Thread(target=worker_loop, name="encode-worker", daemon=True)
    thread.start()
