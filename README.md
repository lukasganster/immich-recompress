# Immich Recompress

Browse, inspect and **recompress** the videos and photos in your
[Immich](https://immich.app/) library to reclaim storage, all from a small web
dashboard. Re-encode videos with HandBrake, recompress JPEGs with ffmpeg or JPEG XL, or
strip the motion clip from Live Photos, with a review-before-replace step and
live progress.

## Screenshots

<p align="center">
  <img src="screenshots/immich-recompress-demo.gif" alt="Immich Recompress workflow: select media, browse the library, and review a recompressed photo" width="100%" />
</p>

## Features

- Browse videos, photos or Live Photos, filtered by size, capture date, album,
  people and location.
- Re-encode videos (HandBrake: x264 / x265 / AV1, optional resolution cap).
- Hardware (GPU) encoding when available, auto-detected at runtime, plus a
  selectable CPU-core count. See [Hardware acceleration](#hardware-acceleration).
- Recompress JPEGs to a target size (ffmpeg, with a macOS `sips` fallback);
  optional opt-in RAW → JPEG.
- Convert JPEGs to lossless JPEG XL, preserving original JPEG reconstruction.
  Review uses a PNG preview; the file uploaded to Immich remains JPEG XL.
- Strip the hidden motion video from Live Photos.
- **Review** the result (new size, savings, preview) before replacing the original.
- Sequential queue with live progress (SSE), proxied thumbnails/downloads and
  persistent job history.

Replacement uploads the compressed file as a new asset, copies the original's
metadata / tags / albums, restores its capture date and location, and moves
the original to the Immich trash (recoverable for the retention period). A
local backup is also kept.

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/lukasganster)

## Requirements

- [`HandBrakeCLI`](https://handbrake.fr/) and [`ffmpeg` / `ffprobe`](https://ffmpeg.org/) on `PATH`
- For JPEG XL: [`cjxl` and `djxl`](https://github.com/libjxl/libjxl) on `PATH`
  (included in Docker; locally install `libjxl-tools` on Debian/Ubuntu or
  `jpeg-xl` with Homebrew).
- An Immich server (tested with **v3.2.2**) and an **admin** API key
- Docker, or for local dev: Python 3.9+ and Node 22 + [pnpm](https://pnpm.io/)

> **Immich compatibility:** Developed and tested against Immich **3.2.2**. Newer or
> older releases may work but API behaviour can differ. Check `/api/status` for the
> detected server version and report issues if something breaks on your version.

Capture-date filters use Immich's structured metadata search API. Dates are
interpreted as whole calendar days in the browser's timezone, including both
selected endpoints. The selected timezone is saved with the filter.

Choose **JPEG XL (lossless)** under **Photo Settings → Output format** to convert
JPEG originals without additional quality loss. Savings depend on the source;
this mode uses the minimum-savings threshold rather than a target percentage.
RAW, other image formats, and existing JPEG XL files are skipped in this mode.
The original JPEG can be reconstructed with `djxl photo.jxl restored.jpg`.

In Select media, search for one album or select people by name. People match any
selected person; album, people, city, state, country and dates are combined.
Location values must match the metadata stored in Immich. Album, people and
location filters require a detected Immich version of 3.2 or later. Older servers
retain size and capture-date browsing.

With multiple API keys, selected libraries define which asset owners are eligible.
Shared albums are available through keys that can read them, but their results
include only assets owned by the selected libraries. Each asset appears once and
uses its owner's API key for processing. Distinct people with the same name stay
separate and show their library labels.

Filters are saved locally. An album or person that becomes unavailable must be
cleared or selected again. Failed or capped scans display an error instead of
partial counts or an apparently complete selection. Narrow the filters and retry
if a scan exceeds the 100-page limit.

## Quick start

> ### ⚠️ Early development
>
> This may still contain bugs, so use it with caution: **I don't guarantee
> against data loss.** Also read [SECURITY.md](SECURITY.md) before exposing it.

### Docker

#### Using the published image (recommended)

A pre-built multi-stage image is published to the GitHub Container Registry on
every release: `ghcr.io/lukasganster/immich-recompress:latest`.

Drop this `docker-compose.yml` somewhere, alongside a `.env` that sets at least
`IMMICH_URL` and `IMMICH_API_KEY` (see [`.env.example`](.env.example)):

```yaml
services:
  immich-recompress:
    image: ghcr.io/lukasganster/immich-recompress:latest
    container_name: immich-recompress
    restart: unless-stopped
    env_file:
      - .env # set IMMICH_URL and IMMICH_API_KEY here
    environment:
      IMMICH_DB: /data/immich_recompress.db
    ports:
      # Bound to 127.0.0.1: this app has NO authentication and can replace/trash
      # Immich media: do not expose it without an authenticating reverse proxy.
      # See SECURITY.md.
      - "127.0.0.1:${PORT:-5050}:${PORT:-5050}"
    volumes:
      - immich-recompress-data:/data # job-history SQLite DB
      - immich-recompress-backups:/tmp/immich_recompress_backup # pre-replace backups
      - immich-recompress-work:/tmp/immich_recompress_ui # download/encode scratch

volumes:
  immich-recompress-data:
  immich-recompress-backups:
  immich-recompress-work:
```

```bash
docker compose up -d
```

For reproducible deploys, pin a version instead of `latest`, e.g.
`ghcr.io/lukasganster/immich-recompress:1.3.1`.

Development PRs target the default branch, `development`. Promote the collected
changes to `main` through a PR with a merge commit when preparing a release.
Release Please then maintains a release PR against `main`.
It prepares the root version and changelog in that PR. Merging the release PR
creates the GitHub release and publishes the container for AMD64 and ARM64.
`latest` follows stable releases;
ordinary merges and prereleases do not change it. The container build runs
after the GitHub release is created, so check that the Release workflow has
finished before pulling a new version. After release, synchronize `main` back
to `development` through a PR with a merge commit. Maintainer setup and recovery
steps are in the [release skill](.agents/skills/release-docker/SKILL.md).

#### Building from source

```bash
pnpm run env                    # creates .env; set IMMICH_URL and IMMICH_API_KEY in it
docker compose up -d --build
```

Then open <http://localhost:5050>.

### Local

```bash
# Backend deps: creates .venv and installs requirements.txt
pnpm run setup

# Create .env, then set IMMICH_URL and IMMICH_API_KEY in it
pnpm run env

# Frontend (Angular, pnpm): builds the UI into backend/static
pnpm run install:frontend && pnpm run build:frontend

# Run (serves UI + API on http://127.0.0.1:5050)
pnpm start
```

The individual steps map to plain commands if you prefer not to use the pnpm
wrappers (`pnpm run setup` → `python3 -m venv .venv && .venv/bin/pip install -r
requirements.txt`, `pnpm start` → `.venv/bin/python backend/server.py`).

## Configuration

Set in `.env` (see [`.env.example`](.env.example)):

| Variable         | Required | Description                                          |
| ---------------- | -------- | ---------------------------------------------------- |
| `IMMICH_API_KEY` | yes      | Immich API key; comma-separate for multiple users    |
| `IMMICH_URL`     | yes      | Base URL of your Immich server                       |
| `PORT`           | no       | Listen port (default `5050`)                         |
| `HOST`           | no       | Dev-server bind address (default `127.0.0.1`)        |
| `IMMICH_DB`      | no       | SQLite job-history path (default `./immich_recompress.db`) |

## Hardware acceleration

The encoder dropdown is detected at runtime from your `HandBrakeCLI` build, so
you only see options that work. Hardware encoders are far faster and barely touch
the CPU; software encoders compress best and expose a **CPU cores** slider
(defaults to all cores. Lower it to keep the machine responsive).

| Platform | Hardware encoder | How to enable |
| -------- | ---------------- | ------------- |
| macOS | Apple VideoToolbox | Works out of the box. |
| Linux + Intel/AMD | QSV / VAAPI | Pass `/dev/dri` (below) + a HandBrake build with QSV/VAAPI. |
| Linux + NVIDIA | NVENC | [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) + GPU reservation (below) + a HandBrake build with NVENC. |

> **Docker:** the stock image's Debian `handbrake-cli` has **no** GPU encoders, so
> only CPU encoders appear. NVENC/QSV need a HandBrake build that includes them.
> The passthrough wiring below (and in `docker-compose.yml`) is ready for one.

```yaml
services:
  immich-recompress:
    devices:
      - /dev/dri:/dev/dri          # Intel/AMD (QSV / VAAPI)
    deploy:                         # NVIDIA NVENC (needs the Container Toolkit)
      resources:
        reservations:
          devices:
            - { driver: nvidia, count: all, capabilities: [gpu] }
```

## Project layout

```
backend/    Flask app (server.py entry; config, state, db, media,
            immich_api, jobs, routes). Builds the UI into backend/static/
frontend/   Angular 22 app (pnpm); see frontend/README.md
```

## Contributing & license

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under
the [MIT License](LICENSE).
