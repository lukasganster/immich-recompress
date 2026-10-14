## Commits

Commit messages and PR titles follow Conventional Commits: `<type>(<optional scope>): <description>`, lowercase and imperative, for example `feat(browse): add capture date filters`. Reference issues in the footer with `Closes #12` or `Refs #12`.

## Pull requests and releases

All changes, including documentation, CI configuration, and version bumps, reach `main` through a pull request. Start new work from `origin/main` on a short branch with a type prefix. Merge with squash using the PR title as the commit title, after the required CI checks pass.

Release Please maintains the release PR with the root `package.json` version and changelog. Merging that PR is the release button for the GitHub release and GHCR image. Keep version changes in the release PR; ordinary feature and fix PRs use Conventional Commits to determine the next version.

For release setup, publishing, or recovery, read [.agents/skills/release-docker/SKILL.md](.agents/skills/release-docker/SKILL.md). Check the workflows and repository rules before claiming automation or branch protection is active.

## Agent skills

### Issue tracker

GitHub Issues, through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
