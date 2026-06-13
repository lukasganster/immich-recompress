"""Immich Optimizer — Flask entrypoint.

A web dashboard to browse, inspect, download and re-encode videos and photos
from an Immich library. All state is kept in memory and lost on restart.

The application is split into focused modules:
  - config       configuration, env loading, formatting helpers
  - state        shared in-memory queue state + cancellation primitives
  - db           SQLite persistence of terminal job history
  - media        ffprobe / HandBrake / ffmpeg command builders
  - immich_api   Immich HTTP client + parsing/summary helpers
  - jobs         job lifecycle, encode pipeline, background worker, SSE
  - routes       Flask blueprint with all HTTP routes

A single background worker thread processes the encoding queue sequentially.
"""
import os
import sys

# Allow `python backend/server.py` (the script's dir, not the project root,
# is on sys.path in that case) in addition to `python -m backend.server` and
# `gunicorn backend.server:app`.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from backend import config  # noqa: F401  (import loads .env + defines constants)
from backend.db import db_init
from backend.jobs import load_persisted_jobs, start_worker
from backend.routes import bp

app = Flask(__name__, static_folder=None)
app.register_blueprint(bp)

# Startup: restore finished-job history and launch the encode worker. Runs at
# import time so it works under gunicorn too (not just `python server.py`).
db_init()
load_persisted_jobs()
start_worker()


if __name__ == "__main__":
    os.makedirs(config.WORK_DIR, exist_ok=True)
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    # Bind to localhost by default: the dashboard has NO authentication and can
    # trash/replace assets in the Immich library, so it must not be exposed on
    # all interfaces unless deliberately placed behind an authenticating proxy.
    # Set HOST=0.0.0.0 explicitly (as the Docker image does) to override.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5050"))
    app.run(host=host, port=port, threaded=True)
