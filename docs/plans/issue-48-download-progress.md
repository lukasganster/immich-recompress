# Investigation and fix plan for issue #48

Issue: https://github.com/lukasganster/immich-recompress/issues/48

Branch: `fix/download-progress`, based on `origin/development` at `d374678`.

Status: implemented and covered by regression tests.

## Reproduction and diagnosis

A temporary Python unittest exercised the real `process_job`,
`download_original`, job state and SSE subscriber queues. Only external HTTP,
metadata, disk capacity, codec probing and CSV logging were stubbed. Each
response supplied four 1 MiB chunks and a valid `Content-Length`. The clock
advanced by 1.1 seconds per chunk without sleeping.

The test observed file sizes after each write and asserted that at least one
`job_update` event reported intermediate progress while still `downloading`.
Two runs produced the same failures:

| Job type | File size after successive writes | Download progress | Intermediate SSE events |
| --- | --- | --- | --- |
| Video | 1, 2, 3, 4 MiB | Always 0% | 0 |
| Photo | 1, 2, 3, 4 MiB | Always 0% | 0 |
| Live Photo | 1, 2, 3, 4 MiB | Always 0% | 0 |

All jobs completed as `downloaded`, confirming that the transfer succeeds.
The harness uses download-only jobs to isolate the transfer from encoders and
replacement. It does not connect to a real Immich instance or browser.

Three candidate causes were checked: missing download reports, lost job-state
updates, and lost SSE payload values. `download_original` has no reporting
callback and only returns after completion or failure. Job callers emit once
before the transfer. `emit_job_update` already includes `progress` and `log`.
`StoreService.applyJobUpdate` already applies both, and the running panel renders
the percentage, meter and log.

A control experiment injected `update_job` and `emit_job_update` after each
chunk in the temporary harness. All three job types then passed, with three
intermediate SSE updates at 25%, 50% and 75%. This validates the job-state and
SSE subscriber path. It is not an implementation or a browser verification.

## Implementation

1. `backend/immich_api.py:download_original` now accepts optional keyword
   arguments `progress_callback` and `total_size`. The return contract is
   unchanged. It counts bytes after a successful write and invokes the callback
   with `(downloaded_bytes, total_bytes)`.
2. It requests `Accept-Encoding: identity` and prefers a positive integer
   `Content-Length` for unencoded responses. It falls back to a positive known
   size if the header is missing, malformed or nonpositive, or if a server
   still compresses the response. This keeps the total in the same units as
   the decoded bytes written by `requests.iter_content`. If neither size is
   available, it reports the byte count without inventing a total.
3. It uses `time.monotonic()` to report at most once per second during the
   transfer, plus an initial/reset state and a final report after the file
   closes. Bytes and the throttle reset for each fresh attempt because the
   destination is opened with `wb`. Cancellation and failures skip the final
   report.
4. `backend/jobs.py:_download_for_job` closes over the owning job ID. It updates
   the existing `progress` and `log` fields and calls `emit_job_update`.
   Job and SSE concerns stay out of the HTTP client.
5. That bridge is used at all five call sites: photo download, video download,
   Live Photo download-only, Live Photo recompression, and the motion backup
   before removal. Videos and photos use `params['size']` as the fallback.
   Live Photos use `video_size` from the linked video's metadata, since the
   queued asset is the still image.

For a known total, the bridge caps progress at 100 and shows a log such as
`Downloading original · 552 MB of 3.26 GB`, using the existing `human_size`
formatter. Motion and backup downloads have specific labels. If both
size sources are unavailable, it keeps updating a log such as
`Downloading original · 552 MB downloaded`; an indeterminate meter would be a
separate small UI enhancement.

The `progress` and `log` fields already travel through both the jobs
snapshot and SSE. This implementation needs no new API fields, frontend models,
database migration or frontend changes for the issue's percentage and byte
display. Encoding already resets progress to zero in all three pipelines, so
download and encoding percentages remain specific to their phases.

Live Photo callbacks target the still-image job ID, while the HTTP request
targets the linked video ID. A regression test checks that progress updates
reach the still job and no motion-video job is created.

While testing retries, network errors were found to be caught as local write
errors because `requests.RequestException` also inherits `OSError`. The handler
order is corrected so transient network failures retry and reset progress.

## Regression coverage and validation

`backend/test_download_progress.py` adds 20 tests. These:

- Exercise the actual streamed downloader and job/SSE path for video, photo
  and Live Photo jobs. Assert intermediate events before transfer completion,
  the correct owning job ID, and the final state.
- Cover all three motion transfer paths, including backup before removal.
- Verify valid `Content-Length` takes priority over a conflicting known size.
  Cover absent, malformed and nonpositive headers, fallback sizes, and unknown
  totals.
- Exercise real gzip decompression through `requests.Response` and
  `urllib3.HTTPResponse`. Encoded responses use the known original size or an
  unknown total, never the compressed wire size. Also verify the identity
  request header and explicit identity responses.
- Use a fake monotonic clock to verify throttling and an unconditional final
  update, including a transfer shorter than one second and empty chunks.
  Byte updates also continue if the fallback underestimates the actual size.
- Verify attempt resets, cancellation and transfer failures cannot report
  successful completion. Check compatibility for callers without a callback.
- Verify the transition from a finished download to encoding starts at zero.

Existing download mocks in JPEG XL, backup cleanup and skip-reason tests accept
the new optional arguments.

Validation commands:

```sh
.venv/bin/python -m unittest backend.test_download_progress -v
.venv/bin/python -m unittest discover -s backend -p 'test_*.py'
.venv/bin/ruff check --select E9,F63,F7,F82 backend/
.venv/bin/python -m compileall -q backend/
git diff --check
```

All 120 backend tests pass, including the 20 new tests. The CI ruff error checks,
compilation, new-test lint/format checks and whitespace checks pass.
The frontend production build and all 63 frontend tests pass. A backend startup
check with a temporary database and `GET /api/jobs` also passes.

The original temporary reproduction also passes with the real fix: each media
type reports 25%, 50% and 75% during transfer. No real Immich instance or browser
was used. Frontend code and its API models are unchanged.
