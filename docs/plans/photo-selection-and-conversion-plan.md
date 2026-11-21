# Photo selection and conversion implementation plan

**Goal:** Select photos by size range, megapixels, format or lossless JPEG XL status; convert non-JPEG photos with an optional megapixel cap while keeping metadata, and page the review bench.

**Architecture:** The browse filters extend the existing scan in `backend/browse.py` and `backend/routes.py`. Conversion adds `backend/photo_convert.py`, which decodes to a PNG intermediate at the source's bit depth and copies metadata with exiftool. `process_image_job` in `backend/jobs.py` calls it. Frontend changes stay in the existing Angular components and `StoreService`.

**Tech Stack:** Flask, Python 3.12 unittest, Angular (signals, standalone components), ffmpeg, libjxl-tools, exiftool, libraw-bin.

**Spec:** `docs/plans/photo-selection-and-conversion-design.md`

## Global constraints

- Run all installs and tests inside podman, never on the host. Mount the repo read-only (`-v "$PWD":/src:ro,Z`) and copy it into the container before installing. Keep `node_modules` and pip caches in named podman volumes. Backend: build a test image from `python:3.12-slim` with `ffmpeg libjxl-tools libimage-exiftool-perl libraw-bin`, then run `pip install -r requirements.txt ruff && ruff check --select E9,F63,F7,F82 backend/ && python -m compileall -q backend/ && python -m unittest discover -s backend -p 'test_*.py'`. Frontend: `node:22.23.4-slim` with `frontend/` mounted, `corepack enable && pnpm install --frozen-lockfile && pnpm run build && pnpm exec ng test --watch=false`. Install a headless browser in the container if the test runner needs one.
- Git runs on the host. Stage explicit paths only.
- One Conventional Commit per task, lowercase and imperative, e.g. `feat(browse): add photo size and megapixel ranges`.
- UI copy and comments follow the writing rules: no em dashes, sentence case, plain words.
- A conversion that can't keep the metadata fails the job and leaves the original in Immich untouched.

## Review focus

1. Asset with no `exifImageWidth`/`exifImageHeight` while a megapixel bound is set: excluded, not crashed or counted as 0 MP that passes `max_mp`.
2. `max_mb` lower than `min_mb`, or not a number: 400 with a clear error, not an empty list.
3. A JXL whose header doesn't fit in 64 KB, or an Immich server that ignores Range and sends the whole file: still classified, and the download is capped so a 200 MB JXL isn't fetched in full during a scan.
4. A TIFF with EXIF orientation 6: the replaced JXL displays upright in Immich and keeps DateTimeOriginal and GPS.
5. Resolving the last review on page 2 of 2: the bench moves to page 1, not an empty page.

Each item has its test in the owning task below.

## Step 0: tool spike, reported before any code

These facts are unverified and each can change Task 2 or Task 5. Check them in a throwaway podman container and report the results first:

- `jxlinfo` on a JXL over 64 KB (a noise image), truncated to 64 KB: does it still print the lossless or lossy line? It may exit non-zero, so read the output regardless of the exit code.
- `dcraw_emu` from Debian's `libraw-bin` on a real CR2 and a real DNG, both public samples downloaded inside the container.
- An Adobe RGB or Display P3 TIFF through `ffmpeg -noautorotate` to PNG, then cjxl: does the ICC profile survive? If not, find the decoder or cjxl flag that keeps it.
- exiftool reading Exif from a cjxl-made JXL. cjxl may Brotli-compress the Exif box, and exiftool then needs an extra Perl module. Which Debian package provides it?

---

### Task 1: Size range, megapixel range and format facets in the scan

**Files:**
- Modify: `backend/browse.py` (`collect` loop around the `summary["size"] < min_bytes` check)
- Modify: `backend/routes.py` (`api_assets`, the cached-summary filter loop, `paginate_summaries`)
- Modify: `backend/immich_api.py` (`video_summary`: add `megapixels` and `format`)
- Test: `backend/test_browse.py`

**Interfaces:**
- Produces: query params `max_mb` (float MB), `min_mp`, `max_mp` (float MP), repeated `format` (uppercase strings). Each summary gains `megapixels: float | None` (width × height / 1e6, rounded to 0.1) and `format: str` (uppercased extension of `originalFileName`, with `JPEG`, `JPE` and `JFIF` folded into `JPG`). The `/api/assets` response gains `formats: dict[str, int]`, counted after every other filter and before the format filter.
- Produces: `photo_format_of(name: str) -> str` in `backend/immich_api.py`.

- [ ] **Step 1: Write failing tests** in `test_browse.py`, following its existing fixtures:
  - `test_max_mb_excludes_larger`: sizes 1, 5 and 20 MB with `min_mb=2&max_mb=10` gives only the 5 MB asset.
  - `test_megapixel_range`: 4000×3000 (12.0 MP), 2000×1500 (3.0 MP), and one with no dimensions, with `min_mp=5` gives only the 12 MP asset. `max_mp=5` gives only the 3 MP asset. The no-dimension asset appears in neither.
  - `test_format_filter_and_facets`: names `a.tif`, `b.TIFF`, `c.dng`, `d.jpeg`, `e.jpg` give `formats == {"TIF": 1, "TIFF": 1, "DNG": 1, "JPG": 2}`. `format=DNG&format=TIF` gives `c.dng` and `a.tif`, and `formats` still lists all four.
  - `test_range_validation`: `min_mb=10&max_mb=5` gives 400. `max_mp=abc` gives 400.
- [ ] **Step 2:** Run the backend tests in podman. The new tests fail.
- [ ] **Step 3:** Implement. Filter on the cached summaries, so only `min_bytes` stays in the scan cache key. Changing max, MP or format then never rescans.
- [ ] **Step 4:** Run the backend tests in podman. All pass.
- [ ] **Step 5:** Commit `feat(browse): add photo size, megapixel and format filters`.

### Task 2: Lossless JPEG XL classification

**Files:**
- Create: `backend/jxl_probe.py`
- Modify: `backend/db.py` (new table), `backend/routes.py` (`jxl_lossless` param), `backend/immich_api.py` if a ranged download helper fits there
- Test: `backend/test_jxl_probe.py`

**Interfaces:**
- Consumes: Task 1 `format` on summaries.
- Produces: `classify_jxl(env: dict, asset_id: str, size: int) -> bool | None` (True for lossless, False for lossy, None when it can't tell), `parse_jxlinfo(output: str) -> bool | None`, and `jxl_is_lossless_cached(asset_id: str, size: int) -> bool | None` backed by a table `jxl_lossless(asset_id TEXT PRIMARY KEY, size INTEGER, lossless INTEGER)`. Param `jxl_lossless=1` keeps only summaries with `format == "JXL"` whose classification is True. Built as: it filters the cached scan in `routes.py` like the other filters, runs last because it downloads, and caches unknown results once the reads worked.
- Classification: GET the original through the owner's key with `Range: bytes=0-65535`, reading at most 64 KB from the stream even when the server answers 200. Write the bytes to a temp file in `WORK_DIR` and run `jxlinfo`. Lossless means the output reports `(possibly) lossless` or JPEG bitstream reconstruction data. If 64 KB isn't enough, retry once with 1 MB, then give up with None (not lossless).

- [ ] **Step 1: Write failing tests.** Generate real fixtures in the test with `cjxl` from a small PNG made by ffmpeg: one with `-d 0` and one with `-d 1`, plus a JPEG converted with `--lossless_jpeg=1`. Skip with `unittest.skipUnless(shutil.which("cjxl"))`.
  - `test_truncated_prefix_classifies`: a noise-image JXL over 64 KB, at `-d 0` and at `-d 1`, cut to 64 KB, still classifies correctly.
  - `test_parse_lossless_and_lossy`: `parse_jxlinfo` on real `jxlinfo` output gives True for `-d 0`, False for `-d 1`, True for the JPEG repack.
  - `test_classify_reads_prefix_only`: mock `requests.get` to stream a 5 MB body, ignoring Range. `classify_jxl` returns the right answer and reads at most 1 MB.
  - `test_cache_invalidates_on_size_change`: a cached True for size 100 is ignored when the size is 200.
  - `test_filter_keeps_only_lossless_jxl`: with `jxl_lossless=1`, a lossy JXL, a lossless JXL and a JPEG give only the lossless JXL.
- [ ] **Step 2:** Run in podman. Fails.
- [ ] **Step 3:** Implement. If `jxlinfo` can't read a 64 KB prefix of the fixtures, report that in your summary.
- [ ] **Step 4:** Run in podman. Passes.
- [ ] **Step 5:** Commit `feat(browse): filter lossless jpeg xl photos`.

### Task 3: Select media dialog and grid format filter

**Files:**
- Modify: `frontend/src/app/services/store.service.ts` (signals `photoMaxMb`, `photoMinMp`, `photoMaxMp` as `number | null`, `jxlLosslessOnly: boolean`, `formatFilter: string[]`, and persist the first four the way `photoMinMb` is persisted)
- Modify: `frontend/src/app/services/api.service.ts` (send `max_mb`, `min_mp`, `max_mp`, `jxl_lossless`, repeated `format` only when set)
- Modify: `frontend/src/app/models/api.models.ts` (`formats?: Record<string, number>` on the list response, `megapixels` and `format` on the summary)
- Modify: `frontend/src/app/components/browse-dialog/browse-dialog.{ts,html}`: max MB input next to the photo threshold, min/max MP number inputs, and a "JPEG XL: lossless only" checkbox on the photo tab. Blank means unbounded.
- Modify: `frontend/src/app/components/media-grid/media-grid.{ts,html}`: a row of toggle buttons, one per `formats` key with its count, photo media only, reset to page 1 on change.
- Modify: `frontend/src/app/app.ts` (fetch subtitle shows `1–10 MB`, `≥ 5 MP` and so on). Use an en dash for ranges, never an em dash.
- Test: the matching `*.spec.ts` files.

**Interfaces:**
- Consumes: Task 1 and Task 2 query params and response fields.

- [ ] **Step 1: Write failing specs:**
  - `api.service.spec.ts`: `max_mb: 10, min_mp: 5, formats: ['TIFF','DNG'], jxl_lossless: true` serialises to `max_mb=10&min_mp=5&format=TIFF&format=DNG&jxl_lossless=1`, and null fields are omitted.
  - `browse-dialog.spec.ts`: applying the dialog with max 10 and min MP 5 sets the store signals. A max below the min shows a validation message and disables apply.
  - `media-grid.spec.ts`: a response with `formats: {TIFF: 2, DNG: 1}` renders two toggle buttons labelled `TIFF (2)` and `DNG (1)`. Selecting one refetches with `format=TIFF` and page 1.
- [ ] **Step 2:** Run the frontend tests in podman. They fail.
- [ ] **Step 3:** Implement, following the patterns of the existing threshold inputs and video codec filter.
- [ ] **Step 4:** Build and test in podman. Passes.
- [ ] **Step 5:** Commit `feat(browse): add range, format and lossless controls to select media`.

### Task 4: Page the review bench

**Files:**
- Modify: `frontend/src/app/components/queue-panel/queue-panel.{ts,html}`
- Test: `frontend/src/app/components/queue-panel/queue-panel.spec.ts` (create it if missing)

**Interfaces:**
- Produces: `REVIEW_PAGE_SIZE = 10`, signal `reviewPage`, getter `pagedReviewJobs`. `reviewJobs`, `reviewSavedBytes` and `replaceAll()` keep covering every review.

- [ ] **Step 1: Write failing specs:**
  - 23 reviews show 10 cards, a "1 of 3" indicator and Next enabled. Page 3 shows 3 cards.
  - "Replace all (23)" stays visible on every page.
  - On page 2 of 2 with 11 reviews, resolving the 11th clamps to page 1 and shows 10 cards.
  - 10 or fewer reviews show no pager.
- [ ] **Step 2:** Run in podman. Fails.
- [ ] **Step 3:** Implement. Clamp the page in a `computed`, not an effect.
- [ ] **Step 4:** Run in podman. Passes.
- [ ] **Step 5:** Commit `feat(review): page the review bench`.

### Task 5: Convert non-JPEG photos with a megapixel cap

**Files:**
- Create: `backend/photo_convert.py`
- Modify: `backend/jobs.py` (`_image_skip_reason`, `process_image_job`, `_encode_jxl_image`, plus the RAW `sips` path so it becomes a fallback)
- Modify: `backend/media.py` if command builders belong there (`build_cjxl_cmd` already does)
- Modify: `backend/routes.py` (accept `photo_max_mp`, a number ≥ 0, where `photo_jxl_distance` is validated near line 748), `backend/jobs.py` job params
- Modify: `frontend/src/app/components/settings-panel/settings-panel.{ts,html}` and `frontend/src/app/models/api.models.ts` (`photo_max_mp`, labelled "Max megapixels", blank or 0 means off, shown in the photo settings summary; `photo_jpeg_chroma`, see below). Both fields are optional in `Settings`, because the store defaults were outside this task.
- Modify: `Dockerfile` and `.github/workflows/ci.yml` (add `libimage-exiftool-perl libio-compress-brotli-perl libraw-bin` to both apt lines). Update the Dockerfile comment that says RAW needs sips.
- Modify: `backend/config.py` comment on `JPEG_PHOTO_EXTS`, which says other inputs are skipped
- Test: `backend/test_photo_convert.py`, plus updates to `backend/test_jpeg_xl.py` and `backend/test_skip_reason.py`, whose expectations change

**Changed while building:** the spike showed cjxl does not copy Exif orientation into the JXL codestream, and djxl rendered a JXL with Exif Orientation 6 sideways. So every decoder now writes upright pixels and Orientation is always reset to 1, which drops `pixels_rotated` and `reset_orientation`. Lossy JPEG to JXL stays on the direct cjxl path, so `needs_pixel_path` no longer takes the output format or distance. ffmpeg input is limited to JPEG, PNG, TIFF and WebP. After review: `still_problem(src, fmt)` skips animated PNG, WebP and JXL and multi-page TIFF before decoding; `has_transparency(path, asset_id)` checks alpha values instead of the pixel format; metadata reads fail closed and compare date and GPS within one tag group; jobs record `pixel_path`. The interfaces below are as built.

**Interfaces:**
- Produces in `photo_convert.py`:
  - `decoder_for(fmt: str) -> str | None`: `"djxl"` for `jxl`, `"dcraw_emu"` (or `"sips"`) for RAW extensions (`RAW_PHOTO_EXTS`), `"ffmpeg"` for JPEG, PNG, TIFF and WebP. Returns None for other formats (HEIC, AVIF, GIF) and when the tool or ffmpeg is missing.
  - `decode_to_png(src: str, fmt: str, out_png: str, max_mp: float | None, asset_id: str) -> tuple[bool, str]` gives `(ok, error)`. The pixels are always upright. It writes an RGB(A) PNG at the source's bit depth (8-bit sources stay 8-bit, so distance-0 output doesn't balloon), keeps the ICC profile through to the encoder, and downscales only when the source exceeds `max_mp`, keeping the aspect ratio. Use ffmpeg's `scale` filter for the resize, after djxl or dcraw_emu output when needed. Every ffmpeg decode passes `-noautorotate`. Run subprocesses through the existing `_run_killable` so cancellation works.
  - `copy_metadata(src: str, dst: str, asset_id: str) -> tuple[bool, str]`: `exiftool -overwrite_original -tagsFromFile src -all:all dst`, then `-Orientation#=1` and the EXIF and XMP dimension tags set to the real output pixels, only where those tags exist. Fails before writing when exiftool warns that it can't read Brotli-compressed metadata. Don't trust the exit code: afterwards, read back DateTimeOriginal (or CreateDate) and fail if the source had it and the output doesn't. Fails when exiftool is missing.
  - `needs_pixel_path(fmt: str, src_mp: float | None, max_mp: float | None) -> bool`: False only for JPEG input with no cap hit, where the existing paths stay. An unknown size with a cap set counts as a hit.
- `_image_skip_reason` stops skipping non-JPEG input. It still skips RAW without `compress_raw`, a missing decoder ("no decoder"), and a missing exiftool when the pixel path is needed ("no exiftool").
- Pixel path for JPEG output: feed the PNG to the existing `compress_image_to_target`, measuring savings against the original size. Pixel path for JXL output: `cjxl png out --distance=<d> --container=1`, since exiftool won't write into a bare codestream. Then run `copy_metadata` and the existing preview, min-savings and replace flow. `upload_name` takes the output extension whenever the format changes.
- Added: JPEG chroma, requested by the owner after reading Jake Archibald's JPEG XL vs AVIF article. `photo_jpeg_chroma` is `source` (default), `444` or `420`, validated in `routes.py` next to `photo_jxl_distance`. `media.jpeg_pix_fmt(src, chroma)` probes the original: a JPEG keeps its 4:2:0, 4:2:2 or 4:4:4, gray takes 4:2:0, and anything else gets 4:4:4, so nothing is downgraded. The settings panel shows a "JPEG chroma" select for JPEG output only.
- Added: the scheduler's disk reservation counts the decoded PNG (width × height × 6 bytes, doubled for RAW and JXL, or 30 times the file size without dimensions), and the JXL preview at 8 bytes per pixel.

- [ ] **Step 1: Write failing tests** with real tools, skipping when one is missing. Make fixtures with ffmpeg in a temp dir, and set EXIF with exiftool.
  - `test_tiff_to_lossy_jxl_keeps_exif`: a 64×48 TIFF with DateTimeOriginal `2019:05:04 10:11:12`, GPSLatitude and Orientation 6 converts at distance 1. Afterwards `exiftool` on the JXL shows the same DateTimeOriginal and GPSLatitude, and Orientation 1, and the pixels are 48×64 (upright). A further test checks all eight orientations against the EXIF definitions.
  - `test_cap_downscales`: a 4000×3000 PNG with `max_mp=3` decodes to 2000×1500. With `max_mp=20` it stays 4000×3000. After a full capped conversion, the image dimensions exiftool reports match the real pixels, so the megapixel filter doesn't keep re-selecting it.
  - `test_lossless_jxl_to_lossy`: a `-d 0` JXL re-encoded at distance 2 comes out smaller than the input and still carries the source's DateTimeOriginal.
  - `test_raw_decode`: `skipUnless` a sample RAW is present at `$RAW_SAMPLE`. A copy tagged Orientation 6 converts to an upright, 16-bit JXL that keeps the capture date.
  - `test_missing_exiftool_fails_job`: patch `shutil.which("exiftool")` to None. A TIFF job gets the skip reason "no exiftool", and the replace function is never called.
  - `test_icc_profile_survives`: an Adobe RGB (or Display P3) TIFF converted to lossy JXL keeps that colour space, checked with `jxlinfo` or exiftool `ICC_Profile:ProfileDescription`.
  - `test_needs_pixel_path`: a table of the cases in the interface.
  - The old "non-JPEG inputs are skipped" case in `test_jpeg_xl.py` now checks that HEIC, AVIF and GIF are skipped with "no decoder".
  - JPEG chroma: 4:4:4 and 4:2:0 JPEG sources with `source`, `444` and `420`, and a TIFF to JPEG, checked with ffprobe `pix_fmt`.
- [ ] **Step 2:** Run in podman. Fails.
- [ ] **Step 3:** Implement. Before relying on `dcraw_emu`, try it in the test image on a real CR2 and a real DNG. Public samples work, e.g. from raw.pixls.us, downloaded inside the container only. If it can't decode them, report back before building on it.
- [ ] **Step 4:** Run the whole backend suite and the frontend build and tests in podman. Passes.
- [ ] **Step 5:** Commit `feat(jobs): convert non-jpeg photos with an optional megapixel cap`.

### Task 6: README

- [ ] Update the README features and photo settings sections to cover the new filters, conversion, the megapixel cap, the JPEG chroma setting, exiftool, the Brotli module and libraw. Note that "replace only if smaller" is the min-savings setting. Commit `docs(readme): describe photo filters and conversion`.
