# Photo selection and conversion

## Why

The owner wants to recompress a photo library in passes, picking photos by size, resolution or format. One example: every photo under X MB goes to JPEG XL, bigger ones to another codec. Another: lossless JPEG XL files get re-encoded to lossy. TIFFs and RAW files are skipped today because only JPEG input is accepted. That has to change.

## Goals

- Select photos by a size range, not only a minimum.
- Select photos by a megapixel range.
- Filter the scanned list by file format, several at once (TIFF, or DNG plus CR2).
- Find JPEG XL files that are lossless.
- Re-encode non-JPEG photos (TIFF, PNG, WebP, JXL, RAW) to JPEG XL or JPEG.
- Cap the output resolution in megapixels.
- Page the review bench.

## Non-goals

- Video changes. The new size max is wired for photos and Live Photo motion clips only.
- A denser review layout. The cards stay as they are, only paged.
- New output formats beyond JPEG and JPEG XL.
- HEIC, AVIF and GIF input. ffmpeg can return a single tile of a tiled HEIC or AVIF, or one frame of a GIF, so these are skipped.

## Anti-goals

- Losing capture dates, GPS or orientation on a replaced photo. A conversion that can't keep the metadata must fail the job and leave the original untouched.
- Replacing anything the user didn't see. Paging the bench must not change what "Replace all" covers or let an unseen review be confirmed.

## Approaches

For format conversion we considered two ways. One adds a special-case path for each input format. The other decodes every non-trivial input to a 16-bit PNG intermediate and runs one encode step on that. We picked the second: it gives one place for the resize cap and one for metadata copying, and adding a format means adding a decoder, not a whole path.

For lossless JPEG XL detection we considered three places. A job-time guard is cheap but leaves the list inaccurate. A full download during the scan is accurate but slow. A partial download during the scan: fetch the first 64 KB of each JXL with a Range request, then run `jxlinfo`. The owner picked browse-time filtering, and the partial download keeps it affordable. Results are cached per asset in SQLite.

For the format filter, the scan's result is the only reliable list of formats present. So the filter lives in the media grid filter bar, populated from the scan response, and applies to the cached scan without a rescan.

All the new filters (max MB, megapixels, format, lossless JPEG XL) run in `routes.py` on the cached scan result, so changing them never rescans. The lossless probe runs last, after the cheaper filters, because it downloads part of each JXL.

For RAW on Linux we picked `dcraw_emu` from Debian's `libraw-bin`. `sips` stays as the macOS fallback.

## Decisions

### Select media dialog

- The photo threshold becomes a range with min MB and max MB. A blank max means no upper bound. The same range applies to the motion clip size on the Live Photo tab. Backend param `max_mb`.
- Min and max megapixels, computed from `exifImageWidth * exifImageHeight` in the scan data Immich already returns. Assets without dimensions are excluded when either bound is set. Backend params `min_mp` and `max_mp`.
- A "JPEG XL: lossless only" checkbox (backend param `jxl_lossless=1`). It keeps only JXL assets classified as lossless. Lossless pixels and JPEG-reconstruction JXL both count. Classification: Range request for the first 64 KB of the original through the owner's key, then `jxlinfo` on the partial file. If 64 KB isn't enough, the builder may grow the range or fall back to the full file. Results are cached in SQLite keyed by asset id plus checksum or size, so a replaced file is classified again. An unknown result is cached too once the reads worked, so a file `jxlinfo` can't classify isn't downloaded again on every scan.

### Format filter

- `/api/assets` returns `formats`, a map of format to count over the result before the format filter (`{"TIFF": 212, "DNG": 40}`). A format is the uppercased extension of `originalFileName`, with `JPEG`, `JPE` and `JFIF` folded into `JPG`.
- Repeated `format` params filter the list. The media grid filter bar shows a row of toggle buttons, one per format with its count, for photo media.

### Encoding pipeline

- JPEG input with no resolution cap hit keeps the direct paths: `--lossless_jpeg=1` at distance 0, and cjxl straight from the JPEG at a lossy distance. cjxl already carries the JPEG's Exif and orientation into the JXL, so a PNG step would add work and gain nothing.
- Every other photo job decodes to a PNG intermediate at the source's bit depth first:
  - ffmpeg for JPEG, PNG, TIFF and WebP only.
  - Before decoding, sources that ffmpeg or djxl would cut down to one image are skipped: animated PNG, WebP and JPEG XL (skip reason "animated"), and TIFFs with more than one full-size page ("multi-page"; reduced-resolution thumbnail pages don't count).
  - djxl for JXL
  - `dcraw_emu` for RAW (still behind the existing "compress RAW" opt-in), with `sips` as the fallback when `dcraw_emu` is missing
- Every decoder writes upright pixels. djxl and `dcraw_emu` rotate on their own; for ffmpeg the EXIF orientation is turned into a transpose or flip filter. Orientation is then reset to 1 on the output. Leaving the pixels as stored and keeping Orientation 6 does not work for JPEG XL: cjxl doesn't copy Exif orientation into the codestream, and JXL decoders follow the codestream, so the photo would show sideways.
- The intermediate is then encoded with cjxl at the chosen distance (0 means lossless pixels), or with the existing JPEG target-savings search. cjxl gets `--container=1` on this path, because exiftool won't add metadata to a bare JXL codestream.
- A transparent source going to JPEG fails the job, since JPEG would drop the alpha. The check reads the actual alpha values of the decoded pixels, so palette PNGs with tRNS transparency are caught, while an opaque alpha channel (lossless WebP, CMYK JPEG) still converts.
- The new photo setting "Max megapixels" (blank or 0 is off) downscales during decode, keeping the aspect ratio, and only when the source is larger.
- After encoding, `exiftool -tagsFromFile` copies metadata from the original, resets Orientation to 1 and updates the dimension tags that exist to the real output size. exiftool's exit code isn't trusted: the capture date and GPS position are read back, each compared within one tag group (EXIF against EXIF, XMP only when the source has no EXIF value, since an XMP date can carry a time zone), and the job errors if either didn't survive. A metadata read that fails or times out fails the job too, rather than counting as "no tags". If exiftool is missing or fails, the job errors and nothing is replaced. JXL files made with `--lossless_jpeg=1` keep their Exif Brotli-compressed, and exiftool reads that only with the `IO::Uncompress::Brotli` Perl module; when the module is missing the job errors rather than copying nothing.
- The upload filename takes the output extension whenever the format changes.
- The existing min-savings check still applies, so a result that isn't smaller is skipped. That already covers "replace only if smaller", so no new setting.
- The job's disk reservation counts the decoded PNG: width × height × 6 bytes, doubled for RAW and JXL whose decoder writes its own copy first, or 30 times the file size when the dimensions are unknown. The JPEG XL review preview counts 8 bytes per pixel (16-bit RGBA).
- The job records which path it took (`pixel_path`), so the review says "Lossless JPEG reconstruction" only for a JPEG repacked without decoding.

### JPEG chroma

- Added after the owner read Jake Archibald's article comparing JPEG XL and AVIF: JPEG output used to force 4:2:0, which halves the colour resolution of 4:4:4 sources.
- New photo setting "JPEG chroma": "Match source" (default), "4:4:4 (full colour)" or "4:2:0 (smaller)". Backend param `photo_jpeg_chroma`: `source`, `444` or `420`.
- Match source never downgrades. A JPEG source keeps its 4:2:0, 4:2:2 or 4:4:4. Anything without subsampling (RGB, 16-bit, CMYK, TIFF, PNG, RAW) gets 4:4:4. Grayscale gets 4:2:0, since it has no colour to lose and mjpeg has no gray mode.
- The original is probed, not the PNG intermediate, which is always RGB.
- JPEG XL needs no option: lossless JPEG transcoding keeps the original's subsampling, and lossy cjxl doesn't subsample.

### Review bench

- 10 reviews per page with prev/next. "Replace all (N)" and the total saved bytes still cover every review. When the last review on a page is resolved, the page clamps back into range.

### Docker image

- Adds `libimage-exiftool-perl`, `libio-compress-brotli-perl` and `libraw-bin`, in the Dockerfile and the CI apt line.

## Constraints

- Run all builds, dependency installs and tests in podman, never on the host.
- One branch off `origin/development`, one Conventional Commit per feature, PR into `development`.

## Left to the builder

- Exact UI layout of the new inputs, following the existing dialog and filter bar patterns.
- The SQLite table shape for the JXL cache.
- Whether `jxlinfo` reads a 64 KB prefix reliably. If not, take the smallest fix that does, and report it.
- Whether Debian's `dcraw_emu` decodes CR2 and DNG samples. If not, report it before building more on it.
