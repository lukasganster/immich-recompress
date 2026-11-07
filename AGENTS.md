## Commits

Every commit message and PR title follows Conventional Commits: `<type>(<optional scope>): <description>`, lowercase and imperative, for example `feat(browse): add capture date filters`. Reference issues in the footer with `Closes #12` or `Refs #12`. Commits land on `development` unchanged and the release notes are built from them, so each commit on a branch is one a reader of the changelog should see.

## Pull requests and releases

`development` is the default branch. Start new work from `origin/development` on a short branch with a type prefix and target every feature, fix, documentation, and CI PR at `development`. Rebase merge those PRs after the required CI checks pass.

`main` is the release branch and always points at a commit of `development`, so both share one linear history. A release is a PR from `development` to `main`, merged by commenting `/fast-forward` once CI is green; the merge button would rewrite the commits. The Release workflow then derives the version from the Conventional Commits since the last tag, creates the tag and GitHub release, and publishes the GHCR image. No version commit and no sync back to `development` is involved.

For release setup, publishing, or recovery, read [.agents/skills/release-docker/SKILL.md](.agents/skills/release-docker/SKILL.md). Check the workflows and repository rules before claiming automation or branch protection is active.

## Agent skills

### Issue tracker

GitHub Issues, through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
