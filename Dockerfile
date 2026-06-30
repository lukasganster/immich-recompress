# syntax=docker/dockerfile:1

# --------------------------------------------------------------------------- #
# Stage 1 — build the Angular frontend into backend/static
# --------------------------------------------------------------------------- #
# Angular 22 requires Node >= 20.19 / 22.12. The build writes to ../backend/static
# (see angular.json "outputPath"), i.e. /app/backend/static.
FROM node:22-slim AS frontend

WORKDIR /app/frontend

# pnpm via corepack (version pinned by package.json "packageManager").
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

# Install deps first so the layer is cached unless the lockfile changes.
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Build the production bundle -> /app/backend/static
COPY frontend/ ./
RUN pnpm run build


# --------------------------------------------------------------------------- #
# Stage 2 — Python runtime with media tooling
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runtime

# ffmpeg/ffprobe (photo recompression + codec probing) and HandBrakeCLI
# (video re-encoding, software x265 works everywhere). `sips` is macOS-only and
# intentionally absent here — the app degrades gracefully (RAW compression off).
#
# Debian's handbrake-cli is built WITHOUT the GPU encoders (NVENC/QSV), so only
# the CPU encoders are detected at runtime here — software multi-core x265/AV1 is
# the out-of-the-box path. To enable hardware encoding, swap in a HandBrake build
# compiled with NVENC/QSV and pass the GPU device into the container (see the
# "Hardware acceleration" section of the README and docker-compose.yml).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        handbrake-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (plus gunicorn as the production WSGI server) — cached separately
# from the source so code edits don't reinstall packages.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Backend source (all package modules) + the frontend bundle built in stage 1.
COPY backend/*.py ./backend/
COPY --from=frontend /app/backend/static ./backend/static

# Default DB location: a /data volume so job history survives container
# recreation (overridable via IMMICH_DB). Pre-created so it works even unmounted.
ENV IMMICH_DB=/data/immich_recompress.db \
    PORT=5050 \
    HOST=0.0.0.0
RUN mkdir -p /data

EXPOSE 5050

# Liveness: /api/status returns 200 even when Immich isn't configured yet.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('PORT','5050'), timeout=4).status==200 else 1)"

# Single gthread worker: the app keeps all queue state in memory and runs one
# background encode thread, so it must not be scaled to multiple workers.
# timeout 0 keeps long-lived SSE (/api/events) connections from being killed.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5050} --workers 1 --threads 8 --worker-class gthread --timeout 0 --pythonpath /app backend.server:app"]
