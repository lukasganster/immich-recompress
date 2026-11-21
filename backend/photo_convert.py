"""Decode photos to a PNG intermediate and carry their metadata to the encoded file."""

import json
import math
import os
import re
import shutil
import subprocess

from backend.config import JPEG_PHOTO_EXTS, RAW_PHOTO_EXTS
from backend.media import probe_image

# Single-frame formats ffmpeg decodes in full. HEIC and AVIF can be tiled and
# GIF animated, and ffmpeg would keep only one tile or frame of them.
FFMPEG_PHOTO_EXTS = JPEG_PHOTO_EXTS | {"bmp", "dib", "png", "tif", "tiff", "webp"}

# ffmpeg filter that turns pixels stored with this EXIF orientation upright.
_UPRIGHT = {2: "hflip", 3: "hflip,vflip", 4: "vflip", 5: "transpose=0",
            6: "transpose=1", 7: "transpose=3", 8: "transpose=2"}


def _run(cmd, asset_id):
    from backend.jobs import _run_killable  # jobs imports this module
    return _run_killable(cmd, asset_id)


def _last_line(output, tool):
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else f"{tool} failed"


def _read_tags(path, *tags, groups="-G0"):
    """First value of each tag per group ("EXIF:DateTimeOriginal"), plus every
    warning. None when exiftool fails, so callers never mistake that for no tags."""
    try:
        result = subprocess.run(["exiftool", "-a", groups, "-s", "-n", "-Warning",
                                 *[f"-{t}" for t in tags], path],
                                capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    values, warnings = {}, []
    for line in result.stdout.splitlines():
        group, _, rest = line.lstrip("[").partition("]")
        name, _, value = rest.partition(":")
        name, value = name.strip(), value.strip()
        if name == "Warning":
            warnings.append(value)
        else:
            values.setdefault(f"{group.strip()}:{name}", value)
    return values, warnings


def decoder_for(fmt):
    """The tool that decodes this format, or None when it is missing or unsafe."""
    fmt = fmt.lower()
    if fmt == "jxl":
        tools = ("djxl",)
    elif fmt in RAW_PHOTO_EXTS:
        tools = ("dcraw_emu", "sips")
    elif fmt in FFMPEG_PHOTO_EXTS:
        tools = ("ffmpeg",)
    else:
        return None
    if not shutil.which("ffmpeg"):  # every decode ends with an ffmpeg pass
        return None
    return next((tool for tool in tools if shutil.which(tool)), None)


def needs_pixel_path(fmt, src_mp, max_mp):
    """JPEG input keeps its direct paths unless the megapixel cap applies.

    An unknown size with a cap set counts as over the cap."""
    if fmt.lower() not in JPEG_PHOTO_EXTS:
        return True
    return bool(max_mp) and (src_mp is None or src_mp > max_mp)


def _is_animated(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(1 << 16)
    except OSError:
        return False
    if head[12:16] == b"VP8X":           # WebP extended header, bit 1 = animation
        return len(head) > 20 and bool(head[20] & 2)
    idat = head.find(b"IDAT")            # APNG declares acTL before the first IDAT
    return head.startswith(b"\x89PNG") and b"acTL" in head[:idat if idat >= 0 else None]


def still_problem(src, fmt):
    """Why decoding would keep only part of the source, as (message, skip reason).
    ("", "") for a single still image."""
    fmt = fmt.lower()
    unreadable = ("Could not check the frames or pages of this photo", "unreadable")
    if fmt == "jxl":
        try:
            info = subprocess.run(["jxlinfo", src], capture_output=True, text=True, timeout=60,
                                  check=False)
        except (OSError, subprocess.SubprocessError):
            return unreadable
        if info.returncode != 0:
            return unreadable
        animated = "JPEG XL animation" in info.stdout
    elif fmt in ("tif", "tiff"):
        read = _read_tags(src, "ImageWidth", "SubfileType", groups="-G1")
        if read is None:
            return unreadable
        tags = read[0]
        # Reduced-resolution IFDs (SubfileType bit 0) are thumbnails, not pages.
        pages = [key for key in tags if re.fullmatch(r"IFD\d+:ImageWidth", key)
                 and not int(tags.get(key.replace("ImageWidth", "SubfileType"), "0") or 0) & 1]
        if len(pages) > 1:
            return "Multi-page TIFFs are not converted", "multi-page"
        animated = False
    else:
        animated = _is_animated(src)
    return ("Animated images are not converted", "animated") if animated else ("", "")


# Immich extracts a motion clip from a still carrying any of these tags
# (metadata.service.ts isMotionPhoto and applyMotionPhotos, v3.4.2).
_MOTION_MARKERS = (b"MicroVideo", b"MotionPhoto")


def _contains_marker(path):
    """Scan the whole file: a missed marker would upload motion tags beside a real link."""
    overlap, previous = max(len(m) for m in _MOTION_MARKERS) - 1, b""
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            window = previous + chunk
            if any(m in window for m in _MOTION_MARKERS):
                return True
            previous = window[-overlap:]
    return False


def motion_trailer(path, quick=True):
    """Length of the motion clip appended to a still: 0 when it has none, None
    when one is declared but the layout can't be confirmed.

    Only a MicroVideoOffset, or a Container directory holding exactly a Primary
    and a MotionPhoto item, is trusted, and only with an MP4 `ftyp` box right
    after the JPEG end marker at the cut. `quick` skips exiftool when no motion
    marker appears anywhere in the file."""
    try:
        size = os.path.getsize(path)
        if quick and not _contains_marker(path):
            return 0
    except OSError:
        return None
    try:
        result = subprocess.run(
            ["exiftool", "-j", "-n", "-struct", "-MicroVideo", "-MicroVideoOffset", "-MotionPhoto",
             "-ContainerDirectory", "-MotionPhotoVideo", "-EmbeddedVideoType", path],
            capture_output=True, text=True, timeout=60, check=False)
        tags = json.loads(result.stdout)[0] if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        tags = None
    if not isinstance(tags, dict):
        return None
    if not (tags.get("MicroVideo") or tags.get("MotionPhoto") or tags.get("MotionPhotoVideo")
            or tags.get("EmbeddedVideoType") == "MotionPhoto_Data"):
        return 0
    length = None
    if tags.get("MicroVideo"):
        length = tags.get("MicroVideoOffset")
    elif tags.get("MotionPhoto") and isinstance(tags.get("ContainerDirectory"), list):
        items = [(entry or {}).get("Item") or {} for entry in tags["ContainerDirectory"]]
        if [item.get("Semantic") for item in items] == ["Primary", "MotionPhoto"] \
                and not items[1].get("Padding"):
            length = items[1].get("Length")
    if isinstance(length, bool) or not isinstance(length, int) or not 2 < length < size - 2:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(size - length - 2)
            cut = fh.read(10)
    except OSError:
        return None
    return length if cut[:2] == b"\xff\xd9" and cut[6:10] == b"ftyp" else None


def strip_motion(src, dst, length, asset_id):
    """Write src without its last `length` bytes and without the motion tags,
    so Immich won't look for a clip that is gone. Returns (ok, error)."""
    try:
        with open(src, "rb") as fh, open(dst, "wb") as out:
            remaining = os.path.getsize(src) - length
            while remaining > 0:
                chunk = fh.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)
    except OSError as exc:
        return False, str(exc)
    rc, output = _run(["exiftool", "-overwrite_original", "-XMP-GCamera:MicroVideo*=",
                       "-XMP-GCamera:MotionPhoto*=", "-XMP-GContainer:all=", dst], asset_id)
    if rc != 0:
        return False, _last_line(output, "exiftool")
    if motion_trailer(dst, quick=False) != 0:
        return False, "The motion tags could not be removed"
    return True, ""


def has_transparency(path, asset_id):
    """True when any pixel is less than fully opaque, palette tRNS included,
    or when that can't be checked."""
    rc, output = _run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                       ("format=rgba,alphaextract,signalstats,"
                        "metadata=print:key=lavfi.signalstats.YMIN:file=-"),
                       "-f", "null", "-"], asset_id)
    lowest = [int(value) for value in re.findall(r"YMIN=(\d+)", output)]
    return rc != 0 or not lowest or min(lowest) < 255


def decode_to_png(src, fmt, out_png, max_mp, asset_id):
    """Write upright pixels at the source's bit depth, with its ICC profile, to out_png.

    Downscales to max_mp megapixels when the source is larger. Returns (ok, error)."""
    decoder = decoder_for(fmt)
    if not decoder:
        return False, f"No decoder for {fmt.upper()}"
    stage, filters = src, []
    if decoder != "ffmpeg":
        stage = out_png + (".png" if decoder == "djxl" else ".tiff")
        cmd = {"djxl": ["djxl", src, stage],
               "dcraw_emu": ["dcraw_emu", "-T", "-6", "-w", "-Z", stage, src],
               "sips": ["sips", "-s", "format", "tiff", src, "--out", stage]}[decoder]
        rc, output = _run(cmd, asset_id)
        if rc != 0 or not os.path.isfile(stage):
            _remove(stage)
            return False, _last_line(output, decoder)
    if decoder in ("ffmpeg", "sips"):
        # djxl and dcraw_emu already turn the pixels upright; these keep the tag.
        read = _read_tags(src, "Orientation")
        if read is None:
            return False, "Could not read the orientation of the original"
        orientation = read[0].get("EXIF:Orientation") or read[0].get("XMP:Orientation", "1")
        if orientation.isdigit() and int(orientation) in _UPRIGHT:
            filters.append(_UPRIGHT[int(orientation)])
    width, height, _ = probe_image(stage)
    if not width or not height:
        if stage != src:
            _remove(stage)
        return False, "Could not read the image size"
    if max_mp and width * height > max_mp * 1e6:
        scale = math.floor(math.sqrt(max_mp * 1e6 / (width * height)) * 1e6) / 1e6
        filters.append(f"scale=trunc(iw*{scale}):trunc(ih*{scale}):flags=lanczos")
    cmd = ["ffmpeg", "-y", "-v", "error", "-noautorotate", "-i", stage, "-frames:v", "1"]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    rc, output = _run(cmd + [out_png], asset_id)
    if stage != src:
        _remove(stage)
    if rc != 0 or not os.path.isfile(out_png) or os.path.getsize(out_png) == 0:
        return False, _last_line(output, "ffmpeg")
    return True, ""


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def copy_metadata(src, dst, asset_id):
    """Copy every tag from src to dst, then mark dst upright and fix its size tags.

    decode_to_png always writes upright pixels, so Orientation becomes 1. Fails
    when the capture date did not make it across, whatever exiftool returned."""
    if not shutil.which("exiftool"):
        return False, "exiftool is not installed"
    checked = ("DateTimeOriginal", "CreateDate", "GPSLatitude", "GPSLongitude")
    read = _read_tags(src, *checked)
    if read is None:
        return False, "Could not read the metadata of the original"
    before, warnings = read
    if any("Brotli" in warning for warning in warnings):
        return False, "exiftool cannot read the compressed metadata (install libio-compress-brotli-perl)"
    commands = [
        ["exiftool", "-overwrite_original", "-tagsFromFile", src, "-all:all", dst],
        # -wm w only updates tags that exist, so nothing new is added.
        ["exiftool", "-overwrite_original", "-wm", "w", "-Orientation#=1", "-tagsFromFile", "@",
         "-ExifIFD:ExifImageWidth<File:ImageWidth", "-ExifIFD:ExifImageHeight<File:ImageHeight",
         "-XMP-exif:PixelXDimension<File:ImageWidth", "-XMP-exif:PixelYDimension<File:ImageHeight",
         "-XMP-tiff:ImageWidth<File:ImageWidth", "-XMP-tiff:ImageLength<File:ImageHeight", dst],
    ]
    for cmd in commands:
        rc, output = _run(cmd, asset_id)
        if rc != 0:
            return False, _last_line(output, "exiftool")
    read = _read_tags(dst, *checked)
    if read is None:
        return False, "Could not read the metadata of the new file"
    after = read[0]
    # Compare within one group: an XMP date can carry a time zone the EXIF one lacks.
    date = next((key for key in ("EXIF:DateTimeOriginal", "EXIF:CreateDate",
                                 "XMP:DateTimeOriginal", "XMP:CreateDate") if before.get(key)), None)
    if date and after.get(date) != before[date]:
        return False, "The capture date was not copied to the new file"
    for name in ("GPSLatitude", "GPSLongitude"):
        key = next((f"{group}:{name}" for group in ("EXIF", "XMP") if before.get(f"{group}:{name}")),
                   None)
        if key and after.get(key) != before[key]:
            return False, "The GPS position was not copied to the new file"
    return True, ""
