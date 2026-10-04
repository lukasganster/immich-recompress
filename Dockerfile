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
# Stage 2 — build current HandBrake CLI with metadata passthrough
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS handbrake-build

ARG HANDBRAKE_VERSION=1.11.2
ARG HANDBRAKE_SHA256=12b046350f2422dc28783ff94229aff4ba5fe5e683431e057355d36163b2593a
# HandBrake's C/C++ build can use substantial memory per compiler job. Keep the
# default low so multi-platform Buildx builds don't exhaust runner memory.
ARG HANDBRAKE_BUILD_JOBS=2

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf automake build-essential cmake curl git libass-dev libbz2-dev \
        libfontconfig-dev libfreetype6-dev libfribidi-dev \
        libharfbuzz-dev libjansson-dev liblzma-dev libmp3lame-dev libnuma-dev \
        libogg-dev libopus-dev libsamplerate0-dev libspeex-dev libtheora-dev \
        libtool libtool-bin libturbojpeg0-dev libvorbis-dev \
        libvpx-dev libx264-dev libxml2-dev m4 make meson nasm ninja-build patch pkg-config \
        python3 tar zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/handbrake-src
RUN curl -fsSLO "https://github.com/HandBrake/HandBrake/releases/download/${HANDBRAKE_VERSION}/HandBrake-${HANDBRAKE_VERSION}-source.tar.bz2" \
    && echo "${HANDBRAKE_SHA256}  HandBrake-${HANDBRAKE_VERSION}-source.tar.bz2" | sha256sum -c - \
    && tar -xjf "HandBrake-${HANDBRAKE_VERSION}-source.tar.bz2" --strip-components=1 \
    && ./configure --disable-gtk --launch-jobs="${HANDBRAKE_BUILD_JOBS}" \
    && make --directory=build --jobs="${HANDBRAKE_BUILD_JOBS}" \
    && make --directory=build install


# --------------------------------------------------------------------------- #
# Stage 3 — Python runtime with media tooling
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runtime

# ffmpeg/ffprobe (photo recompression + codec probing) and timezone data.
# HandBrakeCLI is built from the pinned upstream release above because distro
# packages may not support --keep-metadata.
# `sips` is macOS-only and intentionally absent here — RAW compression degrades
# gracefully when it is unavailable.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libjansson4 \
        libturbojpeg0 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (plus gunicorn as the production WSGI server) — cached separately
# from the source so code edits don't reinstall packages.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Upstream HandBrake install includes its CLI and libhb shared library.
COPY --from=handbrake-build /usr/local/ /usr/local/
RUN ldconfig

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
