---
name: flask-backend
description: Use when working on the Flask/Python backend in backend/ — routes, the encode job pipeline, the Immich API client, SQLite persistence, or shared queue state. Covers how to run the server, lint with ruff, the module architecture, and the threading/state conventions.
---

# Flask backend

The backend lives in `backend/` (package `backend`). It's a single Flask app that serves
both the built Angular UI and a `/api/*` JSON + SSE API. State for the encode queue is
**in memory** (lost on restart); only terminal job history is persisted to SQLite.

## Toolchain

- Python **3.9+** (CI uses 3.12). Runtime deps in `requirements.txt`: `flask`, `werkzeug`,
  `requests`. `gunicorn` is added only in the Docker image, not for local dev.
- A `.venv/` is checked out locally; use `.venv/bin/python` / `.venv/bin/pip`.

## Commands

```bash
# Setup (or: npm run setup from repo root)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Run the dev server -> http://127.0.0.1:5050  (also: npm run dev / npm run start)
.venv/bin/python backend/server.py
# Equivalent entrypoints: python -m backend.server   |   gunicorn backend.server:app

# Lint (ruff 0.15.x is in .venv)
.venv/bin/ruff check backend/                          # full lint
.venv/bin/ruff check --select E9,F63,F7,F82 backend/   # errors-only, as CI gates
python -m compileall backend/                          # CI compile check
```

There is **no backend test suite**; CI gates on the ruff error-subset + `compileall`.
Keep `ruff check --select E9,F63,F7,F82 backend/` clean.

## Module architecture (see `server.py` docstring)

- `config.py` — loads `.env`, defines constants + formatting helpers. Importing it has
  side effects (env load), hence the `# noqa: F401` import in `server.py`.
- `state.py` — shared in-memory queue state + cancellation primitives.
- `db.py` — SQLite persistence of terminal job history (`db_init`, `load_persisted_jobs`).
- `media.py` — builds ffprobe / HandBrake / ffmpeg command lines; also the encoder
  catalog + runtime detection (`ENCODER_CATALOG`, `available_encoders()` parsed from
  `HandBrakeCLI --help`, `cpu_count()`). `build_handbrake_cmd(..., threads)` resolves the
  `-e` name from the catalog, applies presets/`--encopts` thread caps only to software
  encoders. The UI reads detected encoders + cpu count from `GET /api/capabilities`.
- `immich_api.py` — Immich HTTP client + parsing/summary helpers.
- `jobs.py` — job lifecycle, encode pipeline, the background worker, SSE broadcast.
- `routes.py` — the Flask blueprint `bp` with all HTTP routes.
- `server.py` — entrypoint: creates the app (`static_folder=None`), registers `bp`, and at
  **import time** runs `db_init()`, `load_persisted_jobs()`, `start_worker()` so it also
  works under gunicorn.

## Conventions (match these)

- **Shared state lives only in `state.py`** and is mutated **in place** (never reassigned),
  so `from backend.state import _jobs` shares the live object across modules. The one
  rebound value (the active asset id) is read/written via accessor functions
  (`active_id()` / `set_active_id()`) — never `from`-import a rebound name (stale capture).
- A **single background worker thread** processes the encode queue sequentially. Guard
  shared structures with the existing locks in `state.py` (`_state_lock`, `_proc_lock`,
  `_sub_lock`, `_asset_key_lock`); register/kill subprocesses via the `state.py` helpers
  so cancellation works.
- Every module starts with a module docstring; keep that style.
- Add routes to the `bp` blueprint in `routes.py`; the typed frontend expects the existing
  `/api/*` response shapes (cross-check `frontend/src/app/models/api.models.ts`).

## Security note

The dashboard has **no authentication** and can replace/delete assets in the Immich
library, so `server.py` binds `127.0.0.1` by default. Don't change the default bind to
`0.0.0.0`; exposing it is opt-in via the `HOST` env var (the Docker image sets it,
intended to sit behind an authenticating proxy). Keep secrets in `.env`, never commit them.
