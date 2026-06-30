"""Configuration, environment loading and small formatting helpers.

Lowest-level module: imports no project siblings. Importing it loads a
project-root / cwd `.env` so the rest of the app can read config from the env.
"""
import os
import re
import time
from datetime import datetime, timezone

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(*paths):
    """Minimal .env loader (no external dependency).

    Reads simple KEY=VALUE lines from the given files and populates os.environ.
    Existing environment variables always take precedence (shell wins over .env),
    so the first matching file that defines a key sets it. Supports `export KEY=`,
    `# comments`, blank lines and single/double-quoted values.
    """
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key.startswith("export "):
                        key = key[len("export "):].strip()
                    val = val.strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                        val = val[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = val
        except OSError:
            continue


# Load a project-root or current-directory .env so IMMICH_URL / IMMICH_API_KEY
# can be kept in a local, git-ignored file instead of the shell environment.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), os.path.join(os.getcwd(), ".env"))

WORK_DIR = "/tmp/immich_recompress_ui"
BACKUP_DIR = "/tmp/immich_recompress_backup"
CSV_LOG = os.path.join(WORK_DIR, "recompress_log.csv")
# Persisted job history survives restarts (and OS reboots — unlike /tmp), so it
# lives in the project root by default. Override with IMMICH_DB.
DB_PATH = os.environ.get("IMMICH_DB", os.path.join(PROJECT_ROOT, "immich_recompress.db"))

IMMICH_PER_PAGE = 1000      # page size requested from Immich
FRONTEND_PER_PAGE = 100     # page size returned to the browser
USER_CACHE_TTL = 300        # seconds (5 minutes)
HTTP_TIMEOUT = 30           # seconds for normal Immich calls
MIN_FREE_BYTES = 200 * 1024 ** 2   # safety buffer for the work dir
TERMINAL_STATES = {"done", "downloaded", "encoded", "skipped",
                   "error", "cancelled", "discarded"}

# Photos are only *compressed*, never converted: we recompress JPEG in place and
# skip every other format (so the on-disk format never changes). RAW gets its
# own message since converting it would also destroy the original.
JPEG_PHOTO_EXTS = {"jpg", "jpeg", "jpe", "jfif"}
RAW_PHOTO_EXTS = {
    "dng", "cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "raf",
    "orf", "rw2", "raw", "rwl", "pef", "ptx", "srw", "x3f", "3fr", "fff",
    "dcr", "kdc", "mrw", "iiq", "mos", "erf", "mef",
}


def get_env():
    """Return the current Immich-related environment configuration.

    IMMICH_API_KEY may be a comma-separated list of keys (one per user).
    ``api_key`` always holds the first key for backward-compat helpers.
    ``api_keys`` is the full list.
    """
    raw = os.environ.get("IMMICH_API_KEY", "")
    api_keys = [k.strip() for k in raw.split(",") if k.strip()]
    return {
        "api_key": api_keys[0] if api_keys else "",
        "api_keys": api_keys,
        "url": (os.environ.get("IMMICH_URL", "") or "").rstrip("/"),
    }



# Formatting helpers
# --------------------------------------------------------------------------- #

def human_size(num_bytes):
    """Human-readable byte size, e.g. 2147483648 -> '2.0 GB'."""
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def human_duration(seconds):
    """Format a duration in seconds as M:SS (e.g. 185 -> '3:05')."""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "0:00"
    if total < 0:
        total = 0
    if total >= 3600:
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h}:{m:02d}:{s:02d}"
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


def parse_duration(value):
    """Parse Immich duration strings like 'HH:MM:SS.ffffff' into float seconds."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        parts = text.split(":")
        parts = [float(p) for p in parts]
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return 0.0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
