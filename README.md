# Immich Optimizer

Browse, inspect and **recompress** the videos and photos in your
[Immich](https://immich.app/) library to reclaim storage, all from a small web
dashboard. Re-encode videos with HandBrake, recompress JPEGs with ffmpeg, or
strip the motion clip from Live Photos, with a review-before-replace step and
live progress.

## Features

- Browse videos, photos or Live Photos, filtered by size.
- Re-encode videos (HandBrake: x264 / x265 / AV1, optional resolution cap).
- Recompress JPEGs to a target size (ffmpeg, with a macOS `sips` fallback);
  optional opt-in RAW → JPEG.
- Strip the hidden motion video from Live Photos.
- **Review** the result (new size, savings, preview) before replacing the original.
- Sequential queue with live progress (SSE), proxied thumbnails/downloads and
  persistent job history.

Replacement uploads the compressed file as a new asset, copies the original's
metadata / tags / albums, and moves the original to the Immich trash
(recoverable for the retention period). A local backup is also kept.

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/lukasganster)

## Requirements

- [`HandBrakeCLI`](https://handbrake.fr/) and [`ffmpeg` / `ffprobe`](https://ffmpeg.org/) on `PATH`
- An Immich server (tested with **v2.7.4**) and an **admin** API key
- Docker, or for local dev: Python 3.9+ and Node 22 + [pnpm](https://pnpm.io/)

> **Immich compatibility:** Developed and tested against Immich **2.7.4**. Newer or
> older releases may work but API behaviour can differ — check `/api/status` for the
> detected server version and report issues if something breaks on your version.

## Quick start

> ### ⚠️ Early development
>
> This may still contain bugs, so use it with caution: **I don't guarantee
> against data loss.** Also read [SECURITY.md](SECURITY.md) before exposing it.

### Docker

```bash
git clone <repo-url> && cd immich-optimizer
cp .env.example .env            # set IMMICH_URL and IMMICH_API_KEY
docker compose up -d --build
```

Then open <http://localhost:5050>.

### Local

```bash
# Backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set IMMICH_URL and IMMICH_API_KEY

# Frontend (Angular, pnpm): builds the UI into backend/static
cd frontend && pnpm install && pnpm run build && cd ..

# Run (serves UI + API on http://127.0.0.1:5050)
.venv/bin/python backend/server.py
```

## Configuration

Set in `.env` (see [`.env.example`](.env.example)):

| Variable         | Required | Description                                          |
| ---------------- | -------- | ---------------------------------------------------- |
| `IMMICH_API_KEY` | yes      | Immich API key; comma-separate for multiple users    |
| `IMMICH_URL`     | yes      | Base URL of your Immich server                       |
| `PORT`           | no       | Listen port (default `5050`)                         |
| `HOST`           | no       | Dev-server bind address (default `127.0.0.1`)        |
| `IMMICH_DB`      | no       | SQLite job-history path (default `./immich_recompress.db`) |

## Project layout

```
backend/    Flask app (server.py entry; config, state, db, media,
            immich_api, jobs, routes). Builds the UI into backend/static/
frontend/   Angular 22 app (pnpm); see frontend/README.md
```

## Contributing & license

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under
the [MIT License](LICENSE).
