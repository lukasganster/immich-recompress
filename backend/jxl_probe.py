"""Tell lossless JPEG XL photos from lossy ones by reading only the start of each file."""

import os
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from backend import db
from backend.config import HTTP_TIMEOUT, WORK_DIR
from backend.immich_api import _key_env_for_asset, immich_headers

# The header usually fits in 64 KB. A large Exif or JPEG reconstruction box
# before the codestream can push it further, so retry once with 1 MB.
PREFIX_SIZES = (64 * 1024, 1024 * 1024)

# Shared by every request, so the number of downloads at once stays bounded.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jxl-probe")
_inflight = {}
_inflight_lock = threading.Lock()


def parse_jxlinfo(output):
    """True for lossless pixels or a JPEG repack, False for lossy, None when unknown."""
    if "JPEG bitstream reconstruction data" in output:
        return True
    for line in output.splitlines():
        if line.startswith("JPEG XL image,"):
            fields = [field.strip() for field in line.split(",")]
            if "(possibly) lossless" in fields:
                return True
            if "lossy" in fields:
                return False
    return None


def _read_prefix(env, asset_id, limit):
    """Read at most `limit` bytes, even when the server ignores the Range header."""
    headers = {**immich_headers(env), "Accept-Encoding": "identity",
               "Range": f"bytes=0-{limit - 1}"}
    data = bytearray()
    with requests.get(f"{env['url']}/api/assets/{asset_id}/original", headers=headers,
                      stream=True, timeout=HTTP_TIMEOUT) as resp:
        if resp.status_code not in (200, 206):
            return None
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            data += chunk[:limit - len(data)]
            if len(data) >= limit:
                break
    return bytes(data)


def _jxlinfo(data):
    os.makedirs(WORK_DIR, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=WORK_DIR, suffix=".jxl") as fh:
        fh.write(data)
        fh.flush()
        # jxlinfo may exit non-zero on a cut file after printing the header.
        result = subprocess.run(["jxlinfo", fh.name], capture_output=True, text=True,
                                timeout=60, check=False)
    return result.stdout + result.stderr


def _classify(env, asset_id, size):
    """Return (verdict, settled). Settled means the reads worked, so an unknown is final."""
    if not shutil.which("jxlinfo"):
        return None, False
    for limit in PREFIX_SIZES:
        try:
            data = _read_prefix(env, asset_id, limit)
            if not data:
                return None, False
            verdict = parse_jxlinfo(_jxlinfo(data))
        except (requests.RequestException, OSError, subprocess.SubprocessError):
            return None, False
        if verdict is not None or len(data) < limit or (size and limit >= size):
            return verdict, True
    return None, True


def classify_jxl(env, asset_id, size):
    """Classify one JXL original through the owner's key; None when it can't tell."""
    return _classify(env, asset_id, size)[0]


def _probe(env, asset_id, size):
    try:
        verdict, settled = _classify(env, asset_id, size)
        # An unknown is cached too, so a file that fails both reads isn't fetched on every load.
        if settled:
            db.save_jxl_lossless(asset_id, size, verdict)
        return verdict, settled
    except Exception:  # noqa: BLE001 an unexpected failure counts as unchecked, not a failed list
        return None, False
    finally:
        with _inflight_lock:
            _inflight.pop((asset_id, size), None)


def lossless_jxl_only(env, summaries):
    """Keep JXL summaries classified as lossless, using and filling the SQLite cache.

    Returns the kept summaries and how many JXLs couldn't be checked this time.
    """
    jxls = [summary for summary in summaries if summary.get("format") == "JXL"]
    cached = db.jxl_lossless_cached_many([(summary["id"], summary["size"]) for summary in jxls])
    pending = {}
    with _inflight_lock:
        for summary in jxls:
            key = (summary["id"], summary["size"])
            if key[0] in cached:
                continue
            # A probe already running for this asset, from another request, is shared.
            if key not in _inflight:
                _inflight[key] = _pool.submit(_probe, _key_env_for_asset(env, key[0]), *key)
            pending[key[0]] = _inflight[key]
    kept, unchecked = [], 0
    for summary in jxls:
        if summary["id"] in cached:
            verdict = cached[summary["id"]]
        else:
            verdict, settled = pending[summary["id"]].result()
            unchecked += not settled
        if verdict:
            kept.append(summary)
    return kept, unchecked
