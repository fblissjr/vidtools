# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

vidtools is an FFmpeg + PIL + yt-dlp media processing CLI (`vidtools` / `vt` console scripts).

A FastAPI + WebSocket web UI was removed in 0.16.0 — see CHANGELOG. There are
two surfaces: the CLI, and a macOS Finder Quick Action (shipped 0.23.0) under
`macos/`. The Quick Action embeds a `compress` invocation into its bundle at
install time, so a CLI change to `compress` does not reach it until
`macos/install.sh` is re-run.

Python >=3.12. Requires `ffmpeg` >= 5.1 and `yt-dlp` in PATH.

**Homebrew's `ffmpeg` formula is slim** — ten dependencies, no libass, libwebp,
libaom-av1 or libvorbis. `ffmpeg-full` is the batteries-included one. Consequences
in this codebase, all verified against a slim build:

- `subtitles` burn-in needs libass and is unavailable; `add_subtitles` checks for
  the filter and says so rather than emitting a filter-parse error naming no cause.
- The `av1_quality` preset needs libaom-av1, and `.ogg` output needs libvorbis.
  Both are capability-checked via `utils.require_encoder`, which names the gap
  and points at `ffmpeg-full`.
- libwebp is already routed around: `screenshot` and `compress` encode WebP
  through PIL, so they work regardless.

Every filter the tool uses (scale, transpose, crop, afade, loudnorm,
silencedetect, afftdn, acompressor, highpass, aresample) is present in the slim
build. Only encoders and libass are affected.

## Conventions (READ FIRST)

- **uv only**. Never `pip`/`python -m pip`/bare `python`. Run scripts with `uv run`, install with `uv add`, sync with `uv sync`.
- **orjson everywhere**, never stdlib `json`. File I/O: `open(..., "rb")` / `open(..., "wb")` since orjson works with bytes.
- **No `progress_callback` parameter on operations.** Use the thread-local sink: `from .progress import report_progress; report_progress(percent, msg)`. Callers install sinks via `progress_context(sink)`.
- **No `exit(1)` in library code.** Always raise `FfmpegError` (from `vidtools.progress`). The CLI entrypoint catches and exits.
- **No stub handlers.** Only register CLI commands when the operation actually exists.
- **Reuse helpers in `video_ops.py`**: `_add_faststart_if_mp4`, `_add_time_range_args`, `_apply_encode_options`, `_build_scale_filter`, `_build_transpose_filter`, `_escape_subtitle_path`, `_get_video_encoder`, `_TRANSPOSE_MAP`, `DEFAULT_VIDEO_CODEC`, `DEFAULT_AUDIO_CODEC`. Pipeline.py imports them — don't duplicate. Use `_apply_encode_options` for the standard `-c:v / -c:a / -crf|-q:v` block (handles `crf=0` correctly; raw `if crf:` does not).
- All ffmpeg commands include `-y` to avoid interactive stalls.

## Architecture

Modules under `vidtools/`:

- **operations.py** — thin facade (`from .video_ops import *` etc.). Existing imports (`from vidtools.operations import cut_video`) still work. Add new ops to the appropriate `*_ops.py` file, not here.
- **video_ops.py** — FFmpeg video operations + shared filter-builder helpers reused by `pipeline.py`.
- **image_ops.py** — PIL image operations (no ffmpeg). Synchronous; no progress reporting needed. **Every op that renders pixels routes through `_open_image` / `_save_image`** — do not call `Image.open` / `.save` directly. The three inspect-only call sites use `_open_raw` instead (see below). `_open_image` bakes EXIF orientation into the pixels (so geometry ops act on what the user actually sees) and carries the ICC profile across; `_save_image` flattens alpha for containers that can't store it, converts to a mode the container declares in `_FORMAT_MODES`, passes `quality` to every encoder that takes one, re-attaches the ICC profile, and applies the metadata policy via `_build_exif`. The six imaging defects fixed in 0.17.0 were one missing abstraction, not six bugs — that's why they were mutually inconsistent. `strip_image_metadata` is the deliberate exception: it edits the container rather than re-rendering, so it has its own save path.

Three call sites legitimately need the *undecoded* original — `strip_image_metadata` plus the two inspectors (`get_image_info`, `describe_color_profile`), which must not transpose what they report on. They open via **`_open_raw`**, which shares `_open_image`'s hostile-input error translation without applying orientation or ICC. Never call bare `Image.open`: PIL's own exceptions (`DecompressionBombError`, `UnidentifiedImageError`) are not caught by `main()` and reach the user as a traceback printing absolute paths.
- **compress.py** — measured format *and* quality search for images. Encodes candidates with Pillow (no `cwebp`/`avifenc`/`oxipng` subprocesses), scores each against the original with SSIM, and writes the smallest that meets the fidelity target. Full-image SSIM is authoritative; the high-variance probe crop only narrows the starting bracket and can never veto a candidate (it vetoed 7 of 18 real images before 0.18.0). Defaults to PNG/JPEG/WebP — AVIF compresses roughly 2x better but no major vision API decodes it, so it's opt-in behind `--allow-avif` (naming a `.avif` output implies the flag). A content classifier (flat-run fraction + hard-edge density) raises the target to `FLAT_TARGET_SSIM` for text/line art, whose ringing SSIM barely registers. Caveat stated in the module docstring: SSIM is a mediocre perceptual metric, chosen because it's computable with numpy alone and is monotonic in encode quality. Opens via `_open_image`, so EXIF orientation is applied and the ICC profile is carried into every candidate encode — it went through a bare `Image.open` until 0.21.0, which rendered portrait phone photos sideways.
- **audio_ops.py** — FFmpeg audio operations including two-pass loudnorm in `normalize_audio` and the single-pass vocal cleanup chain in `clean_audio`.
- **separation.py** — Audio source separation behind a `SeparationBackend` Protocol. Current backend (`AudioSeparatorBackend`) wraps `audio-separator`. Optional extra: `uv sync --extra separation`. Caches the loaded model by `(model_name, output_dir)` so back-to-back calls don't reload weights.
- **utils.py** — `run_ffmpeg(command)`, plus the ffmpeg **capability probe**
  (`_ffmpeg_capabilities`, `_ffmpeg_has_filter`, `_ffmpeg_has_encoder`,
  `require_encoder`). One probe for filters and encoders both; a *failed* probe
  returns `None` and is deliberately not cached, since caching a transient
  timeout would report a capability as missing until restart. `None` must never
  be treated as "absent" — that reports a missing binary as a missing library. Single mode. Always raises `FfmpegError`. Reports progress via thread-local sink. Coalesces per-frame events to per-percent (no duplicate updates). Also contains `check_ffmpeg_installed()` (>= 5.1) and `is_valid_file()`.
- **progress.py** — Thread-local progress sink (~50 lines). `set_progress_sink`, `get_progress_sink`, `report_progress`, `progress_context` (CM with guaranteed cleanup), `FfmpegError`. Same pattern as Python `logging` / Flask `g`.

- **pipeline.py** — `Pipeline` and `PipelineStep`. Chains video ops into a single ffmpeg invocation with combined `-vf` filter chain. Validates: at most one of each step, no `--copy` (filters require re-encode), `sanitize` requires `--manual-crop`. Cut input args pulled to before `-i`; convert always terminal.
- **cli.py** — argparse setup with handlers defined inline (no separate main.py). `_make_smart` factory + `_detect_kind` for input-type dispatch. `_parse_pipeline_steps` walks argv splitting on op keywords.

- **ytdlp.py** — yt-dlp subprocess wrapper. Reports progress via thread-local sink. Uses `--print after_move:filepath` for reliable output filename detection.
  - `download()` — standard URL download (mp3/wav/mp4) with percent-based progress.
  - `download_live()` — live-broadcast capture (mp4-only). Polls for the show to start (`--wait-for-video`), records from second 0 (`--live-from-start`), survives network blips (`--retries infinite --fragment-retries infinite`). Reports fragment-based progress (throttled to every 5th fragment so multi-hour streams don't emit thousands of progress events).
  - Shared `_run_ytdlp_process(cmd, on_line)` helper handles subprocess lifecycle including graceful Ctrl-C (SIGTERM → yt-dlp finalizes/remuxes the partial file).
- **presets.py** — Preset management with mtime-based cache. JSON file at `presets/ffmpeg_presets.json`.

## Key patterns

1. **Command Flow (CLI)**: argparse → inline handler closure in cli.py → operation in `*_ops.py` → `run_ffmpeg(cmd)`. CLI installs a lazy tqdm sink via `progress_context` in `handle_command`.
2. **Progress Reporting**: operations call `report_progress(percent, msg)`. Sink is `threading.local()`, isolated per worker thread. No-op if no sink installed.
3. **Error Handling**: operations raise `FfmpegError` / `ValueError`. CLI catches in `main()` → exit 1.
4. **Unified verbs + Smart Dispatch**: one verb per concept, no `img-`/`audio-` prefixes (removed in 0.12.0). Multi-media verbs auto-detect input kind via `_detect_kind(path)` (`.gif` is video) and route through `_make_smart`: `info`, `convert`, `resize`, `crop`, `trim` (alias `cut`), `rotate`. Single-media verbs are top-level too: audio = `normalize`/`fade`/`resample`/`clean`/`separate`; image = `thumbnail`/`strip-metadata`/`optimize`/`adjust`; video = `screenshot`/`extract-audio`/`extract-frames`/`concat`/`subtitles`/`sanitize`/`slideshow`/`add-audio`/`match-duration`/`srt-from-transcript`/`dl-live`. `convert` takes a unified `--quality` (1-100) mapped per media (`_quality_to_crf`/`_quality_to_audio_bitrate` in cli.py), with `--crf`/`--bitrate` overrides. The audio-processing verbs (`normalize`/`fade`/`resample`/`clean`) accept video inputs. The routing lives in `video_ops.apply_audio_op_keep_video(in, out, audio_op)` (used by the CLI `_audio_verb` wrapper): audio output runs the op directly; video-in + video-out processes the audio to a temp **`.m4a` (AAC)** and remuxes it over the original video (`add_audio` replace, video copied). The `.m4a`/AAC temp matters — a raw-PCM `.wav` muxes into mp4 as a non-standard stream. `VIDEO_EXTS`/`AUDIO_EXTS`/`IMAGE_EXTS` live in `utils.py` (single source of truth).
5. **Pipelines**: `vidtools pipeline in.mp4 out.mp4 trim --start 60 --end 90 resize -p 0.5 convert --crf 20`. Single ffmpeg invocation. Video only. `trim` and `cut` both map to the internal `cut` step.
6. **Metadata privacy (default on)**: identifying metadata is stripped from output unless the caller opts back in. See the section below — this is the single most surprising thing about the tool's behaviour, and any new op has to make a decision about it.

## Metadata privacy policy

**Identifying metadata is stripped by default.** GPS coordinates, camera
make/model and capture timestamps do not survive into output. `--preserve-metadata`
opts back in. Rendering-critical data — EXIF orientation, ICC colour profile —
is always preserved, because it is part of the image rather than an annotation
on it.

The rationale: these verbs write a *new* file, so the original keeps everything,
while a leaked location does not come back. The dominant use here is
compress-to-share. This deliberately differs from macOS's Convert Image, which
preserves by default.

Granularity differs between media by necessity:

- **Images** (`image_ops._build_exif`) use an *allowlist* — `_PROVENANCE_TAGS`
  (ImageDescription, Artist, Copyright, NewSubfileType). So `--preserve-metadata`
  restores authorship without re-admitting GPS, and a tag nobody has classified
  fails closed. JPEG output also clears the `comment` COM marker explicitly:
  Pillow reads it from `im.info` when the caller supplies none, which bypasses
  the allowlist entirely.
- **Video** (`video_ops._add_metadata_policy`) emits `-map_metadata -1` and
  `-map_metadata:s -1`. ffmpeg's `-map_metadata` is all-or-nothing at the
  container level, so for video `--preserve-metadata` keeps *everything*,
  location included.

Rotation is unaffected, which was the open question blocking the video default:
on a stream copy the display matrix survives `-map_metadata -1` intact; on a
re-encode ffmpeg auto-rotates during decode, so the pixels come out upright and
the matrix is no longer needed.

**Audio inverts the default, deliberately.** For a photo or clip, metadata is
incidental. For audio it is frequently the point -- artist, title and album are
what make a library work, and `convert song.mp3 song.m4a` silently returning an
untitled file would be a worse bug than the leak. So the same three-category
model applies with different membership: descriptive tags (artist, title, album,
track, genre, date) are **kept**, while free-form and recorder fields
(`comment`, `description`, `encoded_by`, `location`, `make`, `model`) are
dropped. `comment` matters most -- it is the field that held 109 KB of
generation prompt in the video case. Implemented by keeping the source map and
blanking specific keys, avoiding a separate ffprobe pass.

**Verbs exposing `--preserve-metadata`**: `convert`, `resize`, `crop`, `trim`,
`rotate`, `concat`, `add-audio`, `match-duration`, `thumbnail`, `optimize`,
`adjust`, `pipeline`, `subtitles`, `normalize`, `fade`, `resample`, `clean`,
`extract-audio`, `extract-frames`, `slideshow`.
Added to a parser via `cli._add_preserve_metadata`.

**`README.md` restates this policy and duplicates the verb list.** Update both, or it silently drifts — it claimed three verbs still leaked for three versions after they were fixed.

`extract-audio` takes the **audio** policy rather than the video one — its
output is an audio file, so title/artist survive while location and comment are
blanked. `extract-frames` and `slideshow` strip.

**Known gaps — do not claim coverage the code does not have:**

- `sanitize` always strips and has no opt-in (by design — that is the verb's job).

## Common tasks

### Run the CLI

```bash
uv run vidtools <command> [options]

# Universal verbs auto-detect type from input extension (no img-/audio- prefixes)
vidtools info file.{mp4,png,mp3}
vidtools resize file.mp4 out.mp4 -p 0.5
vidtools resize file.png out.png -p 0.5
vidtools convert in.wav out.mp3                      # audio convert
vidtools trim --start 60 --end 90 song.mp3 clip.mp3  # alias: cut

# Single-media verbs are also top-level (no prefix)
vidtools normalize in.mp3 out.mp3 --target -16
vidtools thumbnail -s 256 in.png thumb.png

# Pipelines
vidtools pipeline in.mp4 out.mp4 trim --start 60 --end 90 resize -p 0.5 convert --crf 20
```

### Run tests

```bash
uv run pytest tests/ -v
```

17 test files plus `conftest.py`: operations, ytdlp, audio_ops, image_ops, imaging_contract, cli_image_paths, compress, dispatch, progress, pipeline, separation, integration, macos_workflow, macos_quick_action, subtitles, audio_probe, macos_install.

`test_macos_quick_action.py` runs the Quick Action *shell script* under its own
shebang with stubbed `vidtools`/`osascript`. Executing by path rather than
forcing `/bin/zsh` is deliberate — forcing the interpreter masks a regression to
`/bin/bash`, which is 3.2.57 on macOS and dies on the one preset that passes no
flags. macOS-only; skipped elsewhere.

`test_macos_workflow.py` asserts on the dicts `make_workflow.build_workflow` /
`build_info_plist` return. `test_macos_install.py` runs `install.sh` for real
against a temp Services dir (`VIDTOOLS_SERVICES_DIR` overrides the install
location — nothing but tests should set it) and checks bundle *identity* across
a reinstall, which is the one thing a dict assertion cannot see.

Still uncovered: whether macOS actually registers the result. `pbs -dump_pboard`
is the only check for that, and it lags a `pbs -flush` by a few seconds — a
grep immediately after install reads 0 and means nothing.

The Quick Action has shipped four defects, each in a different layer than the
previous fix: shell semantics (bash 3.2 empty-array), output discovery (a glob
matching a stale file), plist contents (metadata subset macOS ignores), and
bundle lifecycle (overwriting in place, so macOS never re-scans). Only the
third is covered here. The pattern to avoid is testing the layer just touched
and calling the feature verified — check the OS's view, per the section above.

Two real-toolchain tiers, separated by marker rather than by file:

- `@pytest.mark.ffmpeg` — needs real `ffmpeg`/`ffprobe` on PATH. `conftest.pytest_collection_modifyitems` skips these, and only these, when the binaries are absent.
- `@pytest.mark.pil_real` — exercises real PIL encode/decode. No external toolchain, so **these must never be gated behind ffmpeg**; that is the whole reason skipping is marker-driven. `test_imaging_contract.py` and `test_cli_image_paths.py` live in this tier — the latter was split out of `test_integration.py` precisely because living there inherited a module-level ffmpeg gate.

Both markers are registered in `pyproject.toml` under `--strict-markers`, so a typo'd marker fails collection rather than silently running everywhere.

Everything else mocks `run_ffmpeg`.

### Verify the Quick Action actually registered

```bash
SERVICES=$HOME/Library/Services   # path-privacy: ignore -- the OS decides where Quick Actions live

/System/Library/CoreServices/pbs -dump_pboard | grep 'Compress Image'   # registered with the services DB
plutil -lint "$SERVICES/Compress Image.workflow"/Contents/*             # both plists parse
plutil -p <a working .workflow>/Contents/document.wflow                 # diff against one Automator wrote
```

`install.sh` replaces the bundle rather than overwriting it — rewriting files in place leaves the bundle directory's mtime unchanged, so macOS keeps its cached registration and the action silently drops out of System Settings -> Login Items & Extensions -> Finder while still working from the right-click menu.

`install.sh` printing "installed:" only means files were written. macOS silently ignores a bundle whose metadata is incomplete, so check the services DB, not the filesystem.

### Generate a test video for manual verification

```bash
ffmpeg -f lavfi -i testsrc=duration=4:size=640x480:rate=30 -y /tmp/test.mp4
```

## Adding a new operation

1. Implement in `video_ops.py`, `image_ops.py`, or `audio_ops.py`. **No `progress_callback` parameter.** Call `run_ffmpeg(command)` for ffmpeg, or `report_progress()` for explicit non-ffmpeg progress.
2. Raise `FfmpegError`/`ValueError` on failure. Never `exit(1)`.
3. For ffmpeg commands: include `-y`. Reuse helpers from `video_ops.py` for filter construction.
   - **Image ops must go through `_open_image` / `_save_image`.** Never `Image.open` / `.save` directly — orientation, ICC profile, alpha flattening, container mode limits and the metadata policy all live there, and every historical imaging bug came from a call site that skipped them.
   - **Decide the metadata question explicitly.** Take a `preserve_metadata: bool = False` parameter, apply `video_ops._add_metadata_policy(command, preserve_metadata)` for ffmpeg ops (`_save_image` handles it for images), and expose the flag with `cli._add_preserve_metadata(parser)`. An op that carries source tags through by default is a leak, not a default.
4. Add CLI command + inline handler closure in `cli.py:setup_argparse()`.
5. Export from `vidtools/__init__.py`.
6. If it's a chainable video filter: wire into `_FILTER_BUILDERS` (pipeline.py) + `_PIPELINE_OP_NAMES` + `_make_step_parser` (cli.py).
7. Write tests. **Mock at `vidtools.<ops_module>.run_ffmpeg`** — patching `vidtools.operations.run_ffmpeg` doesn't work because operations.py is a re-export facade. **Also add a real-ffmpeg test in `tests/test_integration.py` if the op is behavior-sensitive** (stream/container/codec choices, seeking, frame-at-timestamp, encoder availability). Mocked tests verify command *shape*; they cannot catch ffmpeg *behavior*, and that gap is where this project's sharpest bugs lived — `convert→mp3` muxer failure, `screenshot` seek-past-EOF exiting 0 with no file, PCM-in-mp4 from the audio remux, a build missing libwebp. Rule of thumb: if you'd have to run ffmpeg by hand to be sure it works, the unit test won't catch it.

## Testing gotchas

- **Confirm a new test fails for the right reason.** Reintroduce the exact defect and check it goes red. This caught four tests that passed without testing anything: `test_flip_horizontal` asserted only that dimensions survived (so does a no-op), `test_normalize_builds_loudnorm` mocked a stderr shape ffmpeg never emits (so it ran the single-pass fallback for its whole life), and two Quick Action tests were satisfied by a shebang string rather than the behaviour.
- **Review subagents may lack a shell.** Findings reasoned from docs rather than execution are leads, not results — re-run them locally before acting. Here that refuted a claimed glob-metacharacter bug and a claimed argparse leading-dash bug, while a third finding (a bash 3.2 empty-array crash) was real.
- `monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", mock)` — NOT `vidtools.operations.run_ffmpeg`.
- Thread-local progress sink could leak between tests. The `progress_context()` CM auto-clears, and `conftest._isolate_progress_sink` is an autouse suite-wide safety net (it used to live only in `test_progress.py`, so isolation depended on which file a leak originated in).
- Image op tests can use real in-memory PIL images (cheap, no ffmpeg required). Use the shared `conftest.py` fixtures rather than rolling your own: `quadrant_array`/`sample_image` (four distinct quadrants — a flat fill makes flip/rotate correctness untestable in principle, since a real flip and a no-op are pixel-identical on it), `palette_png`, `oriented_jpeg`, `private_jpeg` (carries GPS, for the metadata policy), `wide_gamut_png`/`icc_bytes`, `photo_png`, and `capture_ffmpeg` for command-shape assertions.
- Audio/video op tests mock `run_ffmpeg` (fast, command-shape assertions). For behavior that depends on what ffmpeg actually does, add a real-ffmpeg case in `tests/test_integration.py` instead — it runs the toolchain on a generated clip and auto-skips when ffmpeg is absent. Mocks for shape, integration for behavior.

## Live recording (dl-live)

The `dl-live` CLI command records YouTube/HLS live broadcasts via `vidtools.ytdlp.download_live()`. Designed for shows that only stream live.

**Output is mp4-only.** Two reasons we don't expose `.ts`:

1. Passing `--hls-use-mpegts` OR `-o foo.ts` makes yt-dlp 2026.x invoke ffmpeg with the entire signed HLS master playlist URL as a single `-i` argument. YouTube's signed URLs run ~2 KB and overflow macOS `ARG_MAX` (256 KB), aborting with `OSError: Errno 63 File name too long`. Both triggers must be avoided.
2. yt-dlp's default HLS path writes MPEG-TS data into a `.mp4` file during recording (FixupM3u8 converts to proper mp4 at clean exit), so the partial-file-is-playable property is preserved without forcing `.ts`.

**Known issue (open).** Recording an HLS stream through `download_live()` aborts with `OSError: Errno 63 File name too long`.

The long-standing diagnosis — *"the same flags work from a shell, so Popen is the trigger"* — was wrong, and the wrongness is why it stalled: Popen has no `shell=True` and no env overrides, so there was nowhere to look. Diffing the two commands on 2026-07-20 showed they were never the same. The library adds `--print after_move:filepath` and passes a literal `-o show.mp4` where the shell form used an `-o show.%(ext)s` template. Either is a likelier trigger than the spawn mechanism.

Full analysis, the isolation procedure, and the standalone script live in `internal/scripts/README.md`. **Unverified** — reproducing needs a real live broadcast. `dl-live` is undocumented in the public README while this is open.

## FFmpeg practices

- Stream copy (`-c copy`) by default for cut/concat (no quality loss).
- `--accurate` flag re-encodes for frame-accurate cuts.
- `-movflags +faststart` automatically added for MP4 outputs (`_add_faststart_if_mp4`).
- Aspect-preserving resize uses `-2` (even dimensions), not `-1`.
- GIF conversion uses palette generation for quality.
- Replaced deprecated `-async 1` with `-af aresample=async=1`.
- Hardware acceleration: `--hwaccel` flag uses VideoToolbox on macOS (h264_videotoolbox / hevc_videotoolbox).
- Modern codec presets available: `hevc_quality` (libx265), `av1_quality` (libaom-av1).
- Single-frame grab (`screenshot`): ffmpeg extracts one lossless frame via `_grab_frame`; PIL re-encodes jpg/webp through `image_ops.convert_image` (one quality scale 1-100, and works even when ffmpeg is built without libwebp). Fast input `-ss` by default, output `-ss` for `--accurate`. **Gotcha:** when `-ss` overshoots the input, ffmpeg's image2 muxer writes zero frames and still exits 0, so `_grab_frame` verifies a non-empty file was produced and raises `FfmpegError` otherwise (without this, png grabs silently produce nothing and lossy grabs raise a cryptic PIL error).
- Always work with the latest ffmpeg documentation and features.

## Path privacy

Every path written into repo content must be relative to the repo root. Anything
resolving outside it -- another checkout, a home-directory path, an agent-state
directory -- is a leak, in code, comments, docs, commit messages and branch
names alike.

`pre-commit` and `commit-msg` hooks enforce this and hard-block on a finding. Do
not bypass with `--no-verify`; fix the path and re-stage.

**The hooks live under `.git/hooks`, which git does not track, so a fresh clone
has no protection until they are reinstalled.** Run the `path-privacy` skill's
`install-git-hooks.sh`; the same directory holds `find-external-paths.sh` for an
on-demand audit of the working tree.

A line that genuinely needs an external-looking path (regex source, a worked
example) can carry the literal opt-out token the scanner looks for -- see the
skill's own docs. Use it sparingly.

When fixing a leak, keep it quiet: vague commit message, no changelog entry
naming what was scrubbed. Advertising the fix defeats it.

## Versioning

When bumping version, update three places: `vidtools/_version.py`, `pyproject.toml`, `CHANGELOG.md` (semver, no dates, no major bumps without permission).

## Important notes

- Input and output paths are used as the caller supplied them; relative paths work fine. The exception is where ffmpeg itself resolves paths relative to something other than the working directory — `concatenate_videos` and `slideshow` write `os.path.abspath(...)` entries into the concat-demuxer list file, which ffmpeg resolves relative to the *list file*. (This is about runtime arguments only. Paths written into repo *content* must be repo-relative — see Path privacy above.)
- FFmpeg commands run via subprocess with proper error handling (FfmpegError on non-zero exit).
- Preset modifications are saved to disk immediately; cache invalidates on save.
- **AVIF needs no optional dependency.** Pillow ships AVIF encode/decode natively from 11.3, which is the declared floor; `compress.py` probes with `PIL.features.check("avif")` and degrades to the formats the build has rather than failing. HEIC/HEIF input is **not** supported — nothing in the codebase registers a HEIF opener, so installing `pillow-heif` alone would not enable it.
