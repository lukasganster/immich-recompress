"""SQLite persistence for terminal job history and server-wide settings."""

from contextlib import closing
import sqlite3
import threading
import time

from backend.config import DB_PATH, utcnow_iso

_db_lock = threading.Lock()
# A file that couldn't be classified is tried again after this long.
JXL_UNKNOWN_RETRY_SECONDS = 7 * 24 * 3600
_DB_COLS = ("id", "name", "media", "status", "old_size", "new_size",
            "savings", "codec", "new_codec", "log", "updated_at", "skip_reason")


def db_init():
    try:
        with _db_lock, closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "id TEXT PRIMARY KEY, name TEXT, media TEXT, status TEXT,"
                "old_size INTEGER, new_size INTEGER, savings INTEGER,"
                "codec TEXT, new_codec TEXT, log TEXT, updated_at TEXT,"
                "skip_reason TEXT)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jxl_lossless ("
                "asset_id TEXT PRIMARY KEY, size INTEGER, lossless INTEGER)")
            jxl_columns = {row[1] for row in conn.execute("PRAGMA table_info(jxl_lossless)")}
            if "checked_at" not in jxl_columns:
                conn.execute("ALTER TABLE jxl_lossless ADD COLUMN checked_at REAL")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "skip_reason" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN skip_reason TEXT")
    except sqlite3.Error:
        pass


def db_save_job(job):
    if not job or not job.get("id"):
        return
    row = (job.get("id"), job.get("name"), job.get("media"), job.get("status"),
           job.get("old_size"), job.get("new_size"), job.get("savings"),
           job.get("codec"), job.get("new_codec"), job.get("log"), utcnow_iso(),
           job.get("skip_reason"))
    try:
        with _db_lock, closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs (%s) VALUES (%s)"
                % (",".join(_DB_COLS), ",".join("?" * len(_DB_COLS))), row)
    except sqlite3.Error:
        pass


def db_delete_jobs(ids):
    if not ids:
        return
    try:
        with _db_lock, closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
            conn.executemany("DELETE FROM jobs WHERE id=?", [(i,) for i in ids])
    except sqlite3.Error:
        pass


def db_load_jobs():
    try:
        with _db_lock, closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM jobs").fetchall()]
    except sqlite3.Error:
        return []


def db_load_concurrency() -> int:
    try:
        with _db_lock, closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'concurrent_jobs'"
            ).fetchone()
    except sqlite3.Error:
        return 1
    value = row[0] if row else 1
    return value if type(value) is int and 1 <= value <= 8 else 1


def db_save_concurrency(value: int) -> None:
    with _db_lock, closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('concurrent_jobs', ?)",
            (value,),
        )


def jxl_lossless_cached_many(items):
    """Map asset id to its cached classification for (asset_id, size) pairs.

    Missing ids, changed sizes and unknowns older than the retry window are left
    out. A recent unknown maps to False, so the file stays excluded.
    """
    sizes = dict(items)
    ids = list(sizes)
    rows = []
    try:
        with _db_lock, closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
            for start in range(0, len(ids), 500):  # stay under SQLite's variable limit
                chunk = ids[start:start + 500]
                rows += conn.execute(
                    "SELECT asset_id, size, lossless, checked_at FROM jxl_lossless "
                    f"WHERE asset_id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    except sqlite3.Error:
        return {}
    cutoff = time.time() - JXL_UNKNOWN_RETRY_SECONDS
    return {aid: bool(lossless) for aid, size, lossless, checked_at in rows
            if size == sizes[aid] and (lossless is not None or (checked_at or 0) > cutoff)}


def jxl_is_lossless_cached(asset_id, size):
    """Return a cached classification, or None when missing or the file size changed."""
    return jxl_lossless_cached_many([(asset_id, size)]).get(asset_id)


def save_jxl_lossless(asset_id, size, lossless):
    try:
        with _db_lock, closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO jxl_lossless (asset_id, size, lossless, checked_at)"
                " VALUES (?, ?, ?, ?)",
                (asset_id, size, None if lossless is None else int(lossless), time.time()),
            )
    except sqlite3.Error:
        pass
