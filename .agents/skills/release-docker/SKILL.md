---
name: release-docker
description: Use when cutting a release or publishing the Docker image to GHCR — bumping the version, tagging, what the publish workflow produces, how to pull the image, and how to verify a publish run. Covers ghcr.io/lukasganster/immich-recompress.
---

# Release & Docker publishing

The Docker image is published to the GitHub Container Registry at
**`ghcr.io/lukasganster/immich-recompress`** by `.github/workflows/publish.yml`.
The package is **public** — anyone can pull without authenticating.

## What triggers a publish

`.github/workflows/publish.yml` runs on:

- **push to `main`** → tags `latest`, `main`, and `sha-<commit>`
- **push of a `v*` git tag** → the semver tags below
- **manual** (`workflow_dispatch` from the Actions tab)

It uses the built-in `GITHUB_TOKEN` (`permissions: packages: write`) to log in —
no PAT or repo secret to manage. Build cache is shared via `type=gha`.

## Tag behaviour (docker/metadata-action)

- The `v` prefix is stripped, so the image tag matches `package.json` exactly:
  git tag `v0.1.0-beta.1` → image tag `0.1.0-beta.1`.
- **Prereleases** (e.g. `-beta.1`) deliberately do **not** get the moving
  `latest`, `{{major}}.{{minor}}`, or `{{major}}` tags — a beta won't hijack
  them. Those only move on a stable tag like `v0.1.0`.

## Cutting a release (the flow)

The version lives in the root `package.json` (`"version"`). To release:

```bash
# 1. Bump the version in package.json, then commit it.
git commit -am "chore: release vX.Y.Z"

# 2. Tag with a leading v matching package.json, and push the tag.
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z          # this fires the publish workflow
```

Pushing to `main` also publishes (`latest`/`sha-*`); the git tag is what
produces the immutable, pinnable version tag.

Conventions for this repo: keep `main` and `development` in sync (fast-forward
`main` to `development`); commit messages follow Conventional Commits
(`ci:`, `chore:`, `docs:`…); **do not** add Claude attribution to commits/tags.

## Pulling / running the image

```bash
docker pull ghcr.io/lukasganster/immich-recompress:latest
# or pin a version for reproducible deploys:
docker pull ghcr.io/lukasganster/immich-recompress:0.1.0-beta.1
```

A ready-to-run `docker-compose.yml` template using the published image is in the
README "Docker → Using the published image" section. The app has **no auth** and
can replace/trash Immich media, so the compose port is bound to `127.0.0.1` —
keep it that way unless it sits behind an authenticating reverse proxy.

## Verifying a publish (no `gh`/token needed — repo is public)

```bash
# Were the runs successful?
curl -s "https://api.github.com/repos/lukasganster/immich-recompress/actions/runs?per_page=5" \
  | grep -E '"(display_title|head_branch|status|conclusion)"'

# What tags actually landed on GHCR? (anonymous pull token works for public pkgs)
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:lukasganster/immich-recompress:pull" \
  | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/lukasganster/immich-recompress/tags/list"
```
