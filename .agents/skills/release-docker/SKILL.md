---
name: release-docker
description: Configure or operate release PRs, GitHub releases, and GHCR publishing for immich-recompress, including failed publish recovery.
---

# Releases and Docker publishing

## Release flow

Follow the PR rules in the root `AGENTS.md`. Release Please maintains a release
PR against `main`, collecting changes since the previous release. Its merge is
the release button: `.github/workflows/release.yml` creates the tag and GitHub
release, then calls `.github/workflows/publish.yml` directly to publish GHCR.

Before releasing, inspect those workflows and the repository settings. The
documented flow becomes active when the setup PR is merged and the GitHub
settings below are enabled. If setup is incomplete, report the missing piece
and prepare its fix through a PR.

1. Use Conventional Commits in ordinary PR titles. `fix:` increments patch,
   `feat:` increments minor, and a breaking change increments major. A PR that
   contains only documentation or maintenance need not produce a release.
2. Leave version bumps to the release PR. The root `package.json` is the app
   version; `frontend/package.json` describes the internal frontend package.
   `.release-please-manifest.json` tracks the last released version. The initial
   bootstrap commit in `release-please-config.json` bounds the first changelog.
3. Review the release PR's version and changelog. Merge it when publication is
   requested. Publishing starts automatically after that merge.
4. Verify the GitHub release, workflow result, and image manifest before
   reporting that the release and container are ready. The GitHub release is
   created before the container build, so its existence alone is insufficient.

## Repository setup

- Protect `main` with an active ruleset requiring PRs and the checks named in
  `.github/workflows/ci.yml`. Block force pushes and deletion. Keep the bypass
  list empty. A solo maintainer can require PRs with zero review approvals.
- Enable squash merging with the PR title as the commit title. This preserves
  the Conventional Commit used to calculate the release version.
- Under Settings > Actions > General, enable "Allow GitHub Actions to create
  and approve pull requests". Keep the default token permissions read-only;
  the workflows grant the write permissions needed by each job.
- The default `GITHUB_TOKEN` needs no additional secret. CI on an automatically
  created release PR requires the maintainer to select "Approve workflows to
  run". A GitHub App installation token can remove that additional step if the
  user wants it; app credentials must be configured before changing the token.
- Tags or releases created with `GITHUB_TOKEN` do not start another push or
  release workflow. Preserve the direct reusable-workflow call for publishing.

## Image contract

The public image is `ghcr.io/lukasganster/immich-recompress`, built for
`linux/amd64` and `linux/arm64`. The publisher verifies that the release is
published, the tag matches the root version, and the commit belongs to `main`.
It runs CI for that exact commit before building the image.

Stable releases publish the full version, major.minor, `latest`, and a commit
tag. Prereleases publish the full prerelease version and commit tag only. The
`v` prefix is stripped from image version tags. Ordinary merges do not publish
images. Keep `latest` reserved for stable releases.

Prefer a full version or digest for deployments. The README's Docker section
has the compose template. Preserve its loopback port binding unless an
authenticating reverse proxy protects this app's media replacement operations.

## Verification and recovery

Use `gh run list --workflow release.yml` and `gh run view <run-id> --log-failed`
to inspect the release workflow. Check the release with
`gh release view <tag> --json tagName,isDraft,isPrerelease,url` and inspect both
image platforms with
`docker buildx imagetools inspect ghcr.io/lukasganster/immich-recompress:<version>`.

For a failed container publish, inspect and fix the failure, then use
Actions > Publish Docker image > Run workflow on `main`, supplying the existing
release tag. The CLI equivalent is:

```bash
gh workflow run publish.yml --ref main -f tag=vX.Y.Z
```

This recovery republishes an existing release; it does not bump the version
or create another release. Only the latest stable GitHub release can update
stable image tags, preventing an older recovery from downgrading `latest`.
If recovery fails again for the same reason, report the failure and required
fix instead of repeatedly publishing. Keep release tags in place and prepare
source or workflow fixes through a PR.

If the release workflow fails before creating a GitHub release, inspect the
failure and rerun the Release workflow on `main` after fixing it. If the release
already exists, use the publisher recovery above.
