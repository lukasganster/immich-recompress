## Commits

Commit messages and PR titles follow Conventional Commits: `<type>(<optional scope>): <description>`, lowercase and imperative, for example `feat(browse): add capture date filters`. Reference issues in the footer with `Closes #12` or `Refs #12`.

## Pull requests and releases

`development` is the default branch. Start new work from `origin/development` on a short branch with a type prefix and target ordinary feature, fix, documentation, and CI PRs at `development`. All changes reach `development` and `main` through a PR after the required CI checks pass. Squash ordinary PRs into `development` using the PR title as the commit title.

`main` is the release branch. Promote changes with a PR from `development` to `main`. Use merge commits for every PR targeting `main` so Release Please can read the individual Conventional Commits. Release Please maintains a release PR against `main` with the root `package.json` version and changelog. Merging that release PR is the release button for the GitHub release and GHCR image. Keep version changes in the release PR.

After a release, synchronize `main` back into `development` through a PR with a merge commit. This preserves the shared history and carries the released version and manifest back to development.

For release setup, publishing, or recovery, read [.agents/skills/release-docker/SKILL.md](.agents/skills/release-docker/SKILL.md). Check the workflows and repository rules before claiming automation or branch protection is active.

## Agent skills

### Issue tracker

GitHub Issues, through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
