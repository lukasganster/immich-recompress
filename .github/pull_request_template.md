<!--
Title: use Conventional Commits, for example "fix(api): handle 429 from Immich with backoff".
Keep each section short and delete these comments before submitting.
-->

## Summary

<!-- What changed and why. A small diagram, call tree or diff sketch often says it faster than a paragraph. -->

Closes #

## Evidence

<!-- Show that it works. Screenshots for UI changes, test or console output for everything else. -->

- **Before:**
- **After:**

## Merge Danger

<!--
Door: two-way if this is cheap to roll back, one-way if it is not.
Changes to the encode or replace path can lose media, so call those out here.
-->

**Door:**

**Blast Radius:**

## Checklist

- [ ] Frontend tests pass (`pnpm test` in `frontend/`)
- [ ] `ruff check backend/` shows no new errors and the server still starts
- [ ] UI text is in English
- [ ] README and `.env.example` updated if behaviour or configuration changed
