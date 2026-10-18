# Implementation plan for issue #13

Issue: https://github.com/lukasganster/immich-recompress/issues/13

Branch: `feat/album-metadata-filters`, based on `origin/development`.

## Proposed behavior

Extend Select media with a searchable single-album picker, a searchable people
picker, and city, state and country fields. Combine different filter categories
with AND. If multiple people are selected, match any selected person and label
that behavior in the UI. Use Immich IDs as values and names only as labels.

Recommended library semantics: selected libraries define eligible asset owners.
An album visible through a selected key may contain other users' assets. Only
include assets whose owner has a selected configured library key. This keeps the
result actionable by the existing recompression pipeline. Explain this in the
album control: shared albums are searched within the selected libraries.

These are implementation assumptions, not additional requirements stated in the
issue. If shared assets from owners without configured keys must be displayed,
that requires a separate read-only eligibility state and changes to selection
and queue validation.

All requested filters remain active across pagination, sorting and reloads.
Totals describe the unique, eligible assets after every filter. Failed or capped
scans return an error and cannot become a complete selection.

## Findings in the current code

- `backend/routes.py:api_assets` already accepts dates and caches completed
  summaries. `_structured_capture_filter` provides the starting point for a
  general filter builder. Size, codec, filename and owner checks run locally.
- `backend/immich_api.py:search_metadata_filtered` already pages by cursor, but
  its messages assume date filtering. Missing response blocks can currently
  look like empty success, and repeated cursors are not detected explicitly.
- Legacy scans in `api_assets` and `search_metadata_all` stop on failures or
  page limits and can return partial success. Avoid reusing that behavior for
  the new filters; make browse scans require completion.
- Asset collection does not deduplicate across keys. `_asset_key_map` is assigned
  the discovery key, which can be a shared-album reader rather than the owner.
  `env_for_asset_verified` probes asset readability and falls back to a preferred
  key; neither establishes ownership for shared assets.
- `collect_motion_photos` already applies date constraints to the still image
  and then fetches its linked clip. Generalize that path for metadata filters.
- The Angular dialog uses draft signals and commits them on Apply. Browse state
  persists through `StoreService.saveBrowse`; `MediaGridComponent.load` builds
  requests through `ApiService.assets`. Extend that existing flow.
- Backend unittest coverage exists and runs in CI, despite the older backend
  skill text saying there is no suite. Capture-date tests are in
  `backend/test_config.py`.

## API contract

Require structured-search support for the new album and metadata filters. The
existing capability check uses Immich 3.2+. Preserve legacy browsing and the
legacy capture-date path when none of the new filters is requested. Reject a
new filter on an unsupported or unverified server with an actionable error;
never retry by dropping it. Immich marks `filter` and `cursor` as added in 3.2,
and excludes legacy search fields from structured requests. See the
[official search DTO](https://github.com/immich-app/immich/blob/main/server/src/dtos/search.dto.ts).

Example upstream body:

```json
{
  "filter": {
    "type": { "eq": "IMAGE" },
    "albumIds": { "any": ["<album UUID>"] },
    "personIds": { "any": ["<person UUID>"] },
    "city": { "eq": "Vienna" },
    "country": { "eq": "Austria" },
    "takenAt": { "gte": "2026-01-01T00:00:00Z" }
  },
  "withExif": true,
  "size": 1000
}
```

Omit unset conditions. Send `cursor` on subsequent requests. Keep current local
filename and codec semantics. Keep size filtering local initially, particularly
for Live Photos where the relevant size belongs to the linked clip.

Album-confined search checks album access and can search beyond the normal
user scope. Resolve ownership independently of the key that returned a row. See
the [official search service](https://github.com/immich-app/immich/blob/main/server/src/services/search.service.ts).

## Implementation sequence

1. **Normalize filters and define the browse contract.**
   Add validated `album_id`, repeated `person_id`, `city`, `state` and `country`
   query parameters. Validate UUIDs, trim strings and canonicalize ID sets.
   Generalize the date builder into one structured-filter builder shared by
   video, image and Live Photo scans. Include every normalized filter in the
   asset cache key. Reject malformed explicit key selections rather than
   broadening them to all libraries. Disable Apply when no library is selected;
   keep the existing empty stored array meaning all libraries.

2. **Add metadata discovery scoped to selected keys.**
   Add `GET /api/browse/albums?keys=...` and
   `GET /api/browse/people?keys=...&search=...` with typed response models.
   Fetch album summaries through each selected key, deduplicate by album ID,
   and retain owner information and all accessible key indices. Search the
   summary list by name locally; never fetch an album's full asset collection.
   Immich's album-list API supports owned and shared albums; see the
   [album DTO](https://github.com/immich-app/immich/blob/main/server/src/dtos/album.dto.ts).
   Fetch people through the paginated people endpoint, passing its name filter
   for searches. Preserve person IDs and library labels, including duplicate
   names; never merge distinct IDs by name. See the
   [person DTO](https://github.com/immich-app/immich/blob/main/server/src/dtos/person.dto.ts).
   Scope short-lived caches by server, credentials, selected keys and query.
   Report discovery failures explicitly rather than showing an empty list.

3. **Make filtered collection complete and ownership-aware.**
   Query only selected keys that can access the requested album. Skip a key
   only when discovery established it cannot access that album; an unexpected
   failure from a participating key fails the scan. No accessible key means a
   stale or inaccessible filter error, not an unrestricted query.
   Resolve key owners with `fetch_key_owners`, select an owner key from the
   selected libraries, deduplicate by asset ID, and populate the asset-key map
   with that owner key. Do not infer ownership from a successful GET.
   Reject unresolved key ownership when it prevents establishing the eligible
   result set. Keep person IDs scoped to their discoverable library context;
   never substitute IDs by matching names.
   Generalize structured errors, validate response blocks and item IDs, detect
   repeated cursors, and fail on the scan limit. Ensure legacy browse scans also
   fail instead of caching partial results. Publish cache entries and key-map
   changes only after successful collection. Compute totals after deduplication
   and all local constraints.

4. **Carry filters through Live Photos and queue ownership.**
   Filter still images with `isMotion`, then resolve only their linked clips.
   Fetch clips through the owning key and treat an unreadable linked clip as
   an explicit scan failure rather than a zero-size omission. Preserve the
   motion-video size threshold. Carry the owner key through enqueue and ensure
   worker verification cannot replace it with a non-owner reader key. Inspect
   direct resolve/enqueue paths for conflicting writes to the shared key map.

5. **Wire Select media and persisted state.**
   Extend `api.models.ts`, `ApiService`, `StoreService`, `BrowseDialogComponent`
   and `MediaGridComponent.load`. Add searchable album and people controls with
   library labels, clear actions, loading states and visible errors. Start with
   exact-value city/state/country text fields; geographic map search is outside
   this issue. Reload discovery when selected libraries change, cancel stale
   requests, and explicitly require unavailable persisted choices to be cleared
   or replaced. Never silently clear a requested constraint. Preserve draft
   edits until Apply, reset page and selection on Apply, and persist IDs and
   location values. Existing saved browse settings default to no new filters.
   On scan error, prevent stale rows or selections from being acted on as the
   new result. Show active filter context in the loading/result UI.

6. **Validate behavior and document compatibility.**
   Add mocked backend tests for filter combinations, exact upstream request
   shape, multi-page scans, duplicate assets, shared albums, owner-key mapping,
   same-name people, stale IDs, unsupported versions, upstream failures,
   malformed responses, repeated cursors and page-limit exhaustion. Check cache
   isolation, Live Photo still filtering and linked-clip failures. Extend
   frontend tests for persistence, Apply/Cancel, library changes, stale discovery
   responses, clear actions, request serialization and failed-scan selection.
   Keep date-only legacy and DST regressions covered. Manually check a shared
   album containing assets from two configured owners and one unconfigured
   owner, plus a large result spanning multiple Immich pages.

   Run backend unittest discovery, the CI ruff error subset, Python compilation,
   the Angular production build and one-shot frontend tests. Document the new
   filters, library ownership rule and structured-search requirement in README.
   Verify the actual API permissions and response shapes against a supported
   Immich instance before calling integration behavior validated.

## Delivery

Implement backend collection and regression tests first, then connect the UI.
Keep the work in one feature PR targeting `development`, with
`feat(browse): add album and metadata filters` as the proposed title and
`Closes #13` in its body. No version changes are needed.

## Implementation and verification

Implemented on this branch. Metadata scans use a dedicated `backend/browse.py`
module; the existing date-only builders remain intact for compatibility. The UI
loads paginated people metadata once per selected library set and searches it
locally, rendering at most 50 people at a time with an explicit result count.
Album and people discovery keep independent results and errors. A failed
category blocks only filters that request it; location-only scans remain available.

Verification on 2026-10-18:

- Backend: 34 unittest cases pass, including existing capture-date coverage;
  the CI ruff error subset and Python compilation pass.
- Frontend: production build and 24 tests pass. Component, persistence and request tests
  cover draft edits, unavailable IDs, library changes and discovery failures.
- Browser: desktop (1440 x 1100) and mobile (390 x 844) checks pass for request
  serialization, reopening persisted choices, scan errors, Escape and dialog
  overflow. The Impeccable finish reviewer returned `ship` with no material fixes.
- Read-only Immich 3.3.0 integration: both configured owners resolve; album and
  multi-page people discovery succeed. A shared album's 45 unique photo results
  match independent upstream searches, with correct owner-key assignments.
  Album/location/date composition and person/date searches succeed through the
  supported API. Live Photo behavior and upstream failure cases are covered by
  mocks; the sampled albums contained no Live Photos.

No media was encoded or replaced during verification.
