---
name: release-docker
description: Configure or operate releases, GitHub releases, and GHCR publishing for immich-recompress, including failed publish recovery.
---

# Releases and Docker publishing

## Release flow

Follow the PR rules in the root `AGENTS.md`. `main` only ever moves by
fast-forward to a commit of `development`, and every move of `main` runs
`.github/workflows/release.yml`. That workflow runs
`.github/scripts/release-plan.mjs`, which reads the commits since the newest
stable tag on `main`: a breaking change bumps major, `feat` bumps minor, and
`fix`, `perf` or `revert` bump patch. Other types produce no release. When a
release is due, the workflow creates the tag and GitHub release with the
generated notes, then calls `.github/workflows/publish.yml` directly to publish
GHCR.

1. Add the release to `CHANGELOG.md` through a PR into `development`. On a
   branch from `origin/development`, run
   `node .github/scripts/release-plan.mjs --changelog CHANGELOG.md`, confirm
   the planned version, and commit the file as
   `docs(changelog): add X.Y.Z`. A `docs` commit does not change the plan.
   When no release is due, the script says so and leaves the file alone. If
   more commits reach `development` before the fast-forward, run it again:
   it replaces the section, including when the version has moved.
2. Open a PR from `development` to `main` and wait for CI.
3. Comment `/fast-forward` on the PR when publication is requested.
   `.github/workflows/fast-forward.yml` pushes the PR head to `main`, GitHub
   marks the PR as merged, and the workflow calls the Release workflow. A
   maintainer can do the same from a clone with
   `git push origin <pr-head-sha>:main`, which triggers the Release workflow
   through the push.
4. Verify the GitHub release, workflow result, and image manifest before
   reporting that the release and container are ready. The GitHub release is
   created before the container build, so its existence alone is insufficient.

The merge button on a PR into `main` always creates new commits, which would
split `main` from `development`. If that happened, fast-forward `development`
to `main` through a PR so both point at one commit again.

Versions live in tags only; the root `package.json` has no version. The
`CHANGELOG.md` section and the GitHub release notes come from the same
commits, so they list the same entries.

## Repository setup

- Set `development` as the default branch and protect both `development` and
  `main` with active rulesets requiring PRs, linear history, and the checks
  named in `.github/workflows/ci.yml`. Block force pushes and deletion. Keep
  the bypass lists empty. A solo maintainer can require PRs with zero review
  approvals.
- Allow only rebase merges at repository level and in the `development`
  ruleset. The rulesets accept a fast-forward push of a PR head whose required
  checks passed; that is how `main` moves.
- Under Settings > Actions > General, keep the default token permissions
  read-only; the workflows grant the write permissions needed by each job.
- The default `GITHUB_TOKEN` needs no additional secret. Pushes, tags, and
  releases created with it do not start other workflows, so the fast-forward
  and release workflows call their successors directly. Preserve those calls.

## Image contract

The public image is `ghcr.io/lukasganster/immich-recompress`, built for
`linux/amd64` and `linux/arm64`. The publisher verifies that the release is
published and the tag commit belongs to `main`. It runs CI for that exact
commit before building the image.

Stable releases publish the full version, major.minor, `latest`, and a commit
tag. Prereleases publish the full prerelease version and commit tag only. The
`v` prefix is stripped from image version tags. Ordinary merges do not publish
images. Keep `latest` reserved for stable releases.

Prefer a full version or digest for deployments. The README's Docker section
has the compose template. Preserve its loopback port binding unless an
authenticating reverse proxy protects this app's media replacement operations.

## Verification and recovery

Use `gh run list --workflow release.yml` and `gh run view <run-id> --log-failed`
to inspect the release workflow; after a `/fast-forward`, the run is listed
under the Fast-forward main workflow. Check the release with
`gh release view <tag> --json tagName,isDraft,isPrerelease,url` and inspect both
image platforms with
`docker buildx imagetools inspect ghcr.io/lukasganster/immich-recompress:<version>`.

If the fast-forward fails, the workflow comments on the PR and `main` is
unchanged. Fix the cause and comment `/fast-forward` again.

If the release workflow fails before creating a GitHub release, fix the cause
and run it again with `gh workflow run release.yml --ref main`. It finds the
same commits and creates the missing release.

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
