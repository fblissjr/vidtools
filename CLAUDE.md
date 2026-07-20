# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

vidtools is an FFmpeg + PIL + yt-dlp media processing CLI (`vidtools` / `vt` console scripts).

A FastAPI + WebSocket web UI was removed in 0.16.0 — see CHANGELOG. The CLI is
the only surface; a macOS Quick Action wrapper is planned as the second one.

Python >=3.12. Requires `ffmpeg` >= 5.1 and `yt-dlp` in PATH.

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
- **image_ops.py** — PIL image operations (no ffmpeg). Synchronous; no progress reporting needed.
- **audio_ops.py** — FFmpeg audio operations including two-pass loudnorm in `normalize_audio` and the single-pass vocal cleanup chain in `clean_audio`.
- **separation.py** — Audio source separation behind a `SeparationBackend` Protocol. Current backend (`AudioSeparatorBackend`) wraps `audio-separator`. Optional extra: `uv sync --extra separation`. Caches the loaded model by `(model_name, output_dir)` so back-to-back calls don't reload weights.
- **utils.py** — `run_ffmpeg(command)`. Single mode. Always raises `FfmpegError`. Reports progress via thread-local sink. Coalesces per-frame events to per-percent (no duplicate updates). Also contains `check_ffmpeg_installed()` (>= 5.1) and `is_valid_file()`.
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

10 test files: operations, ytdlp, audio_ops, image_ops, compress, dispatch, progress, pipeline, separation, integration. `test_integration.py` is the only tier that invokes real ffmpeg/PIL (on a generated clip); it auto-skips when ffmpeg/ffprobe are absent. Everything else mocks `run_ffmpeg`.

### Generate a test video for manual verification

```bash
ffmpeg -f lavfi -i testsrc=duration=4:size=640x480:rate=30 -y /tmp/test.mp4
```

## Adding a new operation

1. Implement in `video_ops.py`, `image_ops.py`, or `audio_ops.py`. **No `progress_callback` parameter.** Call `run_ffmpeg(command)` for ffmpeg, or `report_progress()` for explicit non-ffmpeg progress.
2. Raise `FfmpegError`/`ValueError` on failure. Never `exit(1)`.
3. For ffmpeg commands: include `-y`. Reuse helpers from `video_ops.py` for filter construction.
4. Add CLI command + inline handler closure in `cli.py:setup_argparse()`.
5. Export from `vidtools/__init__.py`.
6. If it's a chainable video filter: wire into `_FILTER_BUILDERS` (pipeline.py) + `_PIPELINE_OP_NAMES` + `_make_step_parser` (cli.py).
7. Write tests. **Mock at `vidtools.<ops_module>.run_ffmpeg`** — patching `vidtools.operations.run_ffmpeg` doesn't work because operations.py is a re-export facade. **Also add a real-ffmpeg test in `tests/test_integration.py` if the op is behavior-sensitive** (stream/container/codec choices, seeking, frame-at-timestamp, encoder availability). Mocked tests verify command *shape*; they cannot catch ffmpeg *behavior*, and that gap is where this project's sharpest bugs lived — `convert→mp3` muxer failure, `screenshot` seek-past-EOF exiting 0 with no file, PCM-in-mp4 from the audio remux, a build missing libwebp. Rule of thumb: if you'd have to run ffmpeg by hand to be sure it works, the unit test won't catch it.

## Testing gotchas

- `monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", mock)` — NOT `vidtools.operations.run_ffmpeg`.
- Thread-local progress sink could leak between tests. The `progress_context()` CM auto-clears. `test_progress.py` uses an autouse `_clear_sink` fixture as a safety net.
- Image op tests can use real in-memory PIL images (cheap, no ffmpeg required).
- Audio/video op tests mock `run_ffmpeg` (fast, command-shape assertions). For behavior that depends on what ffmpeg actually does, add a real-ffmpeg case in `tests/test_integration.py` instead — it runs the toolchain on a generated clip and auto-skips when ffmpeg is absent. Mocks for shape, integration for behavior.

## Live recording (dl-live / ytdlp-live)

The `dl-live` CLI command and `ytdlp-live` web op record YouTube/HLS live broadcasts via `vidtools.ytdlp.download_live()`. Designed for shows that only stream live.

**Output is mp4-only.** Two reasons we don't expose `.ts`:

1. Passing `--hls-use-mpegts` OR `-o foo.ts` makes yt-dlp 2026.x invoke ffmpeg with the entire signed HLS master playlist URL as a single `-i` argument. YouTube's signed URLs run ~2 KB and overflow macOS `ARG_MAX` (256 KB), aborting with `OSError: Errno 63 File name too long`. Both triggers must be avoided.
2. yt-dlp's default HLS path writes MPEG-TS data into a `.mp4` file during recording (FixupM3u8 converts to proper mp4 at clean exit), so the partial-file-is-playable property is preserved without forcing `.ts`.

**Known issue (2026-05-16):** when `vidtools.ytdlp.download_live()` invokes yt-dlp via `subprocess.Popen` from Python, yt-dlp's internal ffmpeg invocation still hits the `Errno 63` bug for HLS streams (e.g., YouTube's format 95) — even with `%(ext)s` template output and `--remux-video mp4`. Direct shell invocation of the *same* `yt-dlp` binary with the *same* flags works. The Popen call site has no `shell=True`, no env overrides, no encoding tricks; the trigger has not been isolated. **Workaround**: for live recordings on macOS, invoke `yt-dlp` directly from the shell using the command profile in `vidtools/ytdlp.py:build_ytdlp_live_command`. Reproduces against DASH-served streams too once the format selector lands on HLS. Tracking: investigate yt-dlp source for the offending join, possibly file upstream.

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

## Versioning

When bumping version, update three places: `vidtools/_version.py`, `pyproject.toml`, `CHANGELOG.md` (semver, no dates, no major bumps without permission).

## Important notes

- All file paths in code use absolute paths.
- FFmpeg commands run via subprocess with proper error handling (FfmpegError on non-zero exit).
- Preset modifications are saved to disk immediately; cache invalidates on save.
- Optional dependency: `pillow-heif` for AVIF/HEIF support (not installed by default).

## Deployment

`deploy/setup.sh` sets up a systemd service on Ubuntu. `deploy/vidtools.service` is a hardened unit file (NoNewPrivileges, ProtectSystem=strict, etc.).
