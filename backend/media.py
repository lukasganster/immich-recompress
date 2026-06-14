"""Pure media tooling: ffprobe probing, HandBrake/ffmpeg command builders,
free-space checks and the CSV job log. Holds no job state.
"""
import csv
import json
import os
import shutil
import subprocess

from backend.config import CSV_LOG, ENCODER_MAP, MIN_FREE_BYTES, WORK_DIR

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


def build_handbrake_cmd(src, out, encoder, quality, preset, resolution="original"):
    cmd = ["HandBrakeCLI", "-i", src, "-o", out,
           "-e", ENCODER_MAP.get(encoder, "x265"),
           "-q", str(quality)]
    if encoder == "x265" and preset:
        cmd += ["--encoder-preset", str(preset)]
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
