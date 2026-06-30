"""Pure media tooling: ffprobe probing, HandBrake/ffmpeg command builders,
encoder/CPU capability detection, free-space checks and the CSV job log.
Holds no job state.
"""
import csv
import functools
import json
import os
import shutil
import subprocess

from backend.config import CSV_LOG, MIN_FREE_BYTES, WORK_DIR

def has_free_space(num_bytes):
    """True if the work dir has room for ~2.5x the asset (src + out + backup)."""
    try:
        need = int(num_bytes) * 5 // 2 + MIN_FREE_BYTES
        return shutil.disk_usage(WORK_DIR).free >= need, need
    except OSError:
        return True, 0



def ffprobe_info(path):
    """Return (codec, duration_seconds) for a media file via ffprobe."""
    if not shutil.which("ffprobe"):
        return None, None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None, None
    codec = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            codec = stream.get("codec_name")
            break
    duration = None
    fmt = data.get("format", {})
    if fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None
    return codec, duration


# Maps a resolution choice to a bounding-box edge (px). HandBrake's
# --maxWidth/--maxHeight only ever downscale (never upscale) and preserve aspect
# ratio, so setting both to the same value caps the *long* edge regardless of
# orientation: a 3840×2160 clip → 1920×1080, a 2160×3840 portrait → 1080×1920.
RESOLUTION_LONG_EDGE = {
    "2160": 3840,   # 4K
    "1440": 2560,   # 1440p
    "1080": 1920,   # 1080p
    "720": 1280,    # 720p
    "480": 854,     # 480p
}


# Encoder catalog. Each friendly `id` maps to a HandBrake `-e` name (`hb`), a
# display `label`, whether it's hardware-accelerated (`hw`), a quality spec and
# (software encoders only) the encoder option used to cap the CPU thread count.
#
# Quality scales differ per encoder, so the UI reads min/max/default and the
# `qbetter` direction from here:
#   - "low"  : RF/CRF/QP style, lower number = better quality (x265/x264/NVENC…)
#   - "high" : VideoToolbox constant quality 0–100, higher = better
#
# `threadopt` is the HandBrake `--encopts` key that sets the thread/worker count
# for that software encoder (verified: x265 `pools`, SVT-AV1 `lp`). HandBrake
# manages x264 threading itself and ignores `threads`, so x264 (and every HW
# encoder, where the GPU does the work) leave this None — no core control shown.
ENCODER_CATALOG = [
    {"id": "x265",       "hb": "x265",       "label": "HEVC (x265, CPU)",
     "hw": False, "qmin": 18, "qmax": 32, "qdefault": 24, "qbetter": "low",  "threadopt": "pools"},
    {"id": "svt_av1",    "hb": "svt_av1",    "label": "AV1 (SVT, CPU)",
     "hw": False, "qmin": 18, "qmax": 45, "qdefault": 30, "qbetter": "low",  "threadopt": "lp"},
    {"id": "x264",       "hb": "x264",       "label": "H.264 (x264, CPU)",
     "hw": False, "qmin": 18, "qmax": 32, "qdefault": 22, "qbetter": "low",  "threadopt": None},
    {"id": "vt_h265",    "hb": "vt_h265",    "label": "HEVC (Apple VideoToolbox)",
     "hw": True,  "qmin": 1,  "qmax": 100, "qdefault": 60, "qbetter": "high", "threadopt": None},
    {"id": "nvenc_h265", "hb": "nvenc_h265", "label": "HEVC (NVIDIA NVENC)",
     "hw": True,  "qmin": 18, "qmax": 40, "qdefault": 24, "qbetter": "low",  "threadopt": None},
    {"id": "qsv_h265",   "hb": "qsv_h265",   "label": "HEVC (Intel QSV)",
     "hw": True,  "qmin": 18, "qmax": 40, "qdefault": 24, "qbetter": "low",  "threadopt": None},
    {"id": "vaapi_h265", "hb": "vaapi_h265", "label": "HEVC (VAAPI)",
     "hw": True,  "qmin": 18, "qmax": 40, "qdefault": 24, "qbetter": "low",  "threadopt": None},
    {"id": "vce_h265",   "hb": "vce_h265",   "label": "HEVC (AMD VCE)",
     "hw": True,  "qmin": 18, "qmax": 40, "qdefault": 24, "qbetter": "low",  "threadopt": None},
]

_CATALOG_BY_ID = {e["id"]: e for e in ENCODER_CATALOG}


def encoder_spec(encoder):
    """Catalog entry for a friendly encoder id, defaulting to x265."""
    return _CATALOG_BY_ID.get(encoder, _CATALOG_BY_ID["x265"])


@functools.lru_cache(maxsize=1)
def _handbrake_encoder_names():
    """Parse the `-e/--encoder` block of `HandBrakeCLI --help` into a name set.

    Different HandBrake builds ship different encoders (e.g. vt_* only on macOS,
    nvenc_*/qsv_* only on HW-enabled Linux builds), so we ask the binary rather
    than assume. Cached: the answer can't change without restarting the process.
    """
    hb = shutil.which("HandBrakeCLI")
    if not hb:
        return frozenset()
    try:
        out = subprocess.run([hb, "--help"], capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return frozenset()
    text = (out.stdout or "") + "\n" + (out.stderr or "")
    names, in_block = set(), False
    for line in text.splitlines():
        if "Select video encoder" in line:
            in_block = True
            continue
        if in_block:
            token = line.strip()
            # The list is a contiguous block of single-token, indented names;
            # the next option line ("--encoder-preset …") or any blank ends it.
            if not token or " " in token or token.startswith("-"):
                break
            names.add(token)
    return frozenset(names)


def available_encoders():
    """Catalog entries (UI-facing fields) the running HandBrake build supports."""
    names = _handbrake_encoder_names()
    return [
        {"id": e["id"], "label": e["label"], "hw": e["hw"],
         "qmin": e["qmin"], "qmax": e["qmax"], "qdefault": e["qdefault"],
         "qbetter": e["qbetter"], "cores": bool(e["threadopt"])}
        for e in ENCODER_CATALOG if e["hb"] in names
    ]


@functools.lru_cache(maxsize=1)
def cpu_count():
    """Usable CPU count for the encode worker (the preflight max for core choice).

    Prefers the affinity mask (honours Linux cgroup/`taskset` limits) and falls
    back to the logical CPU count on platforms without sched_getaffinity (macOS).
    """
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return max(1, os.cpu_count() or 1)


def build_handbrake_cmd(src, out, encoder, quality, preset, resolution="original", threads=None):
    spec = encoder_spec(encoder)
    cmd = ["HandBrakeCLI", "-i", src, "-o", out,
           "-e", spec["hb"], "-q", str(quality)]
    # Presets and CPU-thread caps only apply to the software encoders; hardware
    # encoders ignore both (the GPU media engine does the work).
    if not spec["hw"] and preset:
        cmd += ["--encoder-preset", str(preset)]
    if not spec["hw"] and spec["threadopt"] and threads:
        cmd += ["--encopts", f"{spec['threadopt']}={int(threads)}"]
    edge = RESOLUTION_LONG_EDGE.get(str(resolution))
    if edge:
        cmd += ["--maxWidth", str(edge), "--maxHeight", str(edge)]
    cmd += ["--optimize"]
    return cmd


def build_ffmpeg_image_cmd(src, out, quality):
    """Re-encode an image to JPEG via ffmpeg.

    `quality` maps to ffmpeg's -q:v (2 = best/largest, 31 = worst/smallest).
    EXIF/orientation metadata is preserved with -map_metadata. `-pix_fmt
    yuvj420p` forces a JPEG-compatible pixel format, which is required for
    sources that decode to 16-bit / RGB / CMYK (large JPEGs, RAW/DNG previews)
    — the mjpeg encoder rejects those otherwise.
    """
    return ["ffmpeg", "-y", "-noautorotate", "-i", src,
            "-map_metadata", "0", "-q:v", str(quality),
            "-pix_fmt", "yuvj420p", out]


def append_csv_log(row):
    try:
        os.makedirs(WORK_DIR, exist_ok=True)
        exists = os.path.isfile(CSV_LOG)
        with open(CSV_LOG, "a", newline="") as fh:
            writer = csv.writer(fh)
            if not exists:
                writer.writerow(["timestamp", "asset_id", "name", "codec",
                                 "old_size", "new_size", "savings", "status"])
            writer.writerow(row)
    except OSError:
        pass
