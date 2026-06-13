"""SQLite persistence for terminal job history (survives restarts)."""
import sqlite3
import threading

from backend.config import DB_PATH, utcnow_iso

_db_lock = threading.Lock()
_DB_COLS = ("id", "name", "media", "status", "old_size", "new_size",
            "savings", "codec", "new_codec", "log", "updated_at")


def db_init():
    try:
        with _db_lock, sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "id TEXT PRIMARY KEY, name TEXT, media TEXT, status TEXT,"
                "old_size INTEGER, new_size INTEGER, savings INTEGER,"
                "codec TEXT, new_codec TEXT, log TEXT, updated_at TEXT)")
    except sqlite3.Error:
        pass


def db_save_job(job):
    if not job or not job.get("id"):
        return
    row = (job.get("id"), job.get("name"), job.get("media"), job.get("status"),
           job.get("old_size"), job.get("new_size"), job.get("savings"),
           job.get("codec"), job.get("new_codec"), job.get("log"), utcnow_iso())
    try:
        with _db_lock, sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs (%s) VALUES (%s)"
                % (",".join(_DB_COLS), ",".join("?" * len(_DB_COLS))), row)
    except sqlite3.Error:
        pass


def db_delete_jobs(ids):
    if not ids:
        return
    try:
        with _db_lock, sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.executemany("DELETE FROM jobs WHERE id=?", [(i,) for i in ids])
    except sqlite3.Error:
        pass


def db_load_jobs():
    try:
        with _db_lock, sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM jobs").fetchall()]
    except sqlite3.Error:
        return []
