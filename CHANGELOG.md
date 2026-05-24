# Changelog

## 0.14.1

### Fixed
- `convert <video> <audio> --quality N` (e.g. `convert clip.mp4 out.mp3 -q 95`) now honors `--quality`. Audio-only output is routed to the audio encoder (quality→bitrate) regardless of input type; previously a video input went through the video path where `--quality` became an unused CRF and was silently dropped.
- The CLI reports operation validation errors (bad rotation, invalid crop geometry, missing fade args, non-numeric rotate angle) as a clean `Error: ...` with exit 1 instead of a raw traceback — `main()` now catches `ValueError` alongside `FfmpegError`.
- Audio verbs on a video input writing a video output (`normalize`/`fade`/`resample`/`clean clip.mp4 out.mp4`) now produce AAC audio, not raw PCM — the latter muxed into mp4 as a non-standard stream many players reject.
- Web **Clean Audio** on a video upload keeps the video now (processes the audio track and remuxes), matching the CLI; previously it dropped the video stream.
- `rotate` on an image accepts non-integer angles (e.g. `45.5`) and gives a clear message on non-numeric input.

### Internal
- Extracted `video_ops.apply_audio_op_keep_video` as the single audio-or-remux router, shared by the CLI audio verbs and the web `clean` dispatch.
- Moved the media-type extension sets (`VIDEO_EXTS`/`AUDIO_EXTS`/`IMAGE_EXTS`) to `utils.py` as the single source of truth.

## 0.14.0

### Added
- Web UI parity: **Clean Audio** and **Match Duration** are now in the Process dropdown (Screenshot was already there), so the file-in/file-out CLI ops are reachable from the phone. `separate` (multi-output) and `dl-live` (URL/live) intentionally stay out of the single-file form.
- **Real-ffmpeg integration test tier** (`tests/test_integration.py`): runs the actual ffmpeg/PIL toolchain on a generated clip for the high-risk paths (convert→mp3 stream handling, screenshot seek-past-EOF, audio-verb-on-video remux, stream-copy trim, resize, webp-via-PIL). Skips automatically when ffmpeg/ffprobe aren't on PATH. Complements the mocked unit tests, which only verify command shape.

No CLI/op behavior changes.

### Fixed
- `vidtools preset` with no sub-action now prints help instead of crashing with `AttributeError: 'Namespace' object has no attribute 'func'`. `handle_command` also guards against a missing `func` defensively.

### Internal
- Simplification pass over the unified-CLI code: `convert` now derives the output extension with `Path(...).suffix` (consistent with the rest of cli.py); clarified the `crop_video` validation docstring (it checks geometry signs, not full bounds).

## 0.13.0

### Added
- **Audio verbs now accept video inputs.** `normalize`, `fade`, `resample`, and `clean` work on a video: an audio output (e.g. `out.mp3`) extracts and processes the audio; a video output (e.g. `out.mp4`) processes the audio track and remuxes it back over the original video (video stream copied). So `vidtools normalize clip.mp4 out.mp4` fixes the audio and keeps the picture.

### Changed
- `--quality` → CRF mapping recentered so `--quality 50` lands on the x264 default CRF 23 (was ~28); 100 → 16 (near-lossless), 1 → ~30.
- Pipeline `convert` step flags aligned with the top-level CLI: `--video-codec`/`--audio-codec`/`--video-bitrate`/`--audio-bitrate` (were `--vcodec`/`--acodec`/`--vbitrate`/`--abitrate`).

### Fixed
- `convert`/`screenshot` to WebP at `--quality 100` now writes a true lossless WebP (matches "higher = better").
- Web UI quality field parsing uses an explicit radix and guards `NaN`.

## 0.12.0

### Changed (breaking: CLI command surface unified)
- **One verb per concept; media-prefixed commands removed.** The `img-*` and `audio-*` command families are gone. Every operation is now a top-level verb. Multi-media verbs auto-detect the input type: `info`, `convert`, `resize`, `crop`, `trim`, `rotate` all work on whatever you hand them (video/audio/image as applicable) without you choosing a variant.
- **`cut` → `trim`** (canonical), with `cut` kept as an alias. `trim` works on both video and audio.
- **`trim-to-audio` → `match-duration`** (clearer, and out of the "trim" namespace).
- Audio ops dropped their prefix: `audio-normalize`→`normalize`, `audio-fade`→`fade`, `audio-resample`→`resample`, `audio-clean`→`clean`, `audio-separate`→`separate`, `audio-convert`/`audio-cut`/`audio-info` folded into `convert`/`trim`/`info`.
- Image ops dropped their prefix: `img-thumb`→`thumbnail`, `img-strip-exif`→`strip-metadata`, `img-optimize`→`optimize`, `img-adjust`→`adjust`, `img-resize`/`img-convert`/`img-crop`/`img-info` folded into `resize`/`convert`/`crop`/`info`.
- **Unified `--quality` (1-100, higher = better)** on `convert`, mapped per media (video→CRF, image→PIL quality, audio→bitrate). Power-user overrides `--crf` (video) and `--bitrate` (audio) still win. Removed the old hack where `--crf` was reinterpreted as image quality.
- Consistent flag names: `--video-codec`/`--audio-codec` everywhere (dropped `--vcodec`/`--acodec`/`--codec`); `--start`/`--end`/`--duration` for all time ranges. `trim` dropped the vestigial `--copy` flag (default is stream-copy; `--accurate` re-encodes) and the duplicate `-to`/`--to`.
- `rotate` now works on images too (any angle), not just video.
- Web UI + API op keys renamed to match (`cut`→`trim`, `trim-to-audio`→`match-duration`, `audio-clean`→`clean`, `audio-separate`→`separate`).

### Fixed
- `crop` on video now validates geometry (positive W/H, non-negative offsets) and raises `ValueError`, matching the image path (previously ffmpeg failed late/opaquely).
- README no longer documents the non-existent `video-resize`/`video-convert`/`video-cut` commands.

## 0.11.1

### Fixed
- `convert` with an audio-only output (e.g. `vidtools convert video.mp4 out.mp3`) no longer fails with "Nothing was written into output file". `convert_format` now detects an audio-only output container (via the shared `AUDIO_CODEC_MAP`), drops the video stream with `-vn`, and selects the correct codec (`libmp3lame`, `pcm_s16le`, etc.) instead of forcing `-c:v libx264 -c:a aac` into the audio muxer. Time-range flags are preserved. Fixes both the CLI and the web `convert` dispatch.

## 0.11.0

### Added
- **`screenshot`**: grab a single high-quality still frame from a video at a timestamp. `vidtools screenshot in.mp4 shot.png --at 00:01:30`. Output format is inferred from the extension (`.png`/`.jpg`/`.webp`). PNG is lossless; jpg/webp re-encode via PIL with `--quality` (1-100, higher = better). `--accurate` decodes to the exact frame (slower) instead of the default fast keyframe seek.
- ffmpeg extracts the frame (to a lossless PNG for lossy targets); PIL handles jpg/webp encoding so the quality scale stays consistent and webp works even when ffmpeg is built without libwebp.
- Exposed on the web UI as the `screenshot` op (timestamp/format/quality/accurate), reachable from the mobile-first dropdown.

## 0.10.0

### Added
- **`dl-live`**: record YouTube/HLS live broadcasts that only stream live. Polls until the broadcast starts (`--wait`, default 60s), captures from the beginning (`--live-from-start`), and survives long broadcasts via infinite fragment retries. Output is mp4-only; yt-dlp writes MPEG-TS data into the .mp4 during recording and FixupM3u8 converts to proper mp4 at clean exit, so partial-file playability is preserved either way.
- `--from-now` joins at the live edge instead of recording from the start. `--no-wait` fails immediately if the broadcast isn't live yet.
- Exposed on the web as the `ytdlp-live` op; same flag surface via job params (`wait_interval`, `from_start`).
- `examples/record_live.py`: standalone, zero-deps version of the same recipe (only needs `yt-dlp` on PATH). Useful for understanding which flags matter and why; also documents the macOS `ARG_MAX` issue with `.ts` output and `--hls-use-mpegts`.

### Changed
- `_make_cli_sink` now surfaces text messages via `tqdm.set_postfix_str`. Lets fragment-style live progress show real-time updates ("Recording fragment 142") even though the percent bar can't advance meaningfully for an unbounded stream.

### Known issues (macOS, yt-dlp 2026.x)
- When `download_live()` invokes yt-dlp via `subprocess.Popen` for an HLS-served stream (e.g., YouTube's format 95), yt-dlp's internal ffmpeg invocation aborts with `OSError: Errno 63 File name too long`. The very same `yt-dlp` binary with the very same flag list works when invoked directly from the shell. Trigger has not been isolated. Workaround: for live recordings on macOS, invoke `yt-dlp` directly. See `CLAUDE.md` "Live recording" section.

### Dependencies
- `yt-dlp`: floor raised from `>=2024.0.0` to `>=2026.3.17`. Both versions can hit the `ARG_MAX` bug above; the bump is for general extractor freshness.

## 0.9.0

### Added
- **`trim-to-audio`**: trim a video so its duration matches the audio track plus a configurable buffer. For clips whose video track outlasts the audio (e.g. avatar/TTS-generated videos with trailing silent frames). Probes each stream's duration via ffprobe and applies `-t (audio_duration + buffer)`. Buffer defaults to 0.25s.
- Stream-copies by default (fast, lossless, keyframe-imprecise). `--accurate` re-encodes for frame-accurate trimming with optional `--crf`/`--video-codec`/`--audio-codec`/`--hwaccel`.
- No-op when the audio is already at least as long as the video (file is still remuxed to the output path so callers get a consistent artifact).
- Wired into `_OP_DISPATCH` so the web UI / JobManager can run it as a regular job.
- Not added to the pipeline (requires a probe pass; same constraint as `sanitize` without `--manual-crop`).

- **`audio-clean`**: single-pass vocal cleanup chain. Defaults to the canonical recipe `highpass=80 -> afftdn=nf=-25 -> acompressor(threshold=-15dB,ratio=3,attack=5,release=50) -> loudnorm(I=-14,TP=-1.0,LRA=11)`. Output codec auto-detected from extension; mp3 defaults to libmp3lame VBR `-q:a 2`.
- Each stage is independently skippable via `--no-highpass`, `--no-denoise`, `--no-compress`, `--no-loudnorm`. All parameters individually tunable from CLI; full param surface in the Python API (`vidtools.audio_ops.clean_audio`).
- Wired into `_OP_DISPATCH` for web/job execution.

- **`audio-separate`** (optional extra): audio source separation via `audio-separator`. Mel-band roformer, BS-roformer, Demucs, MDX-Net, VR. CoreML accelerated on Apple silicon via ONNXRuntime. Auto-downloads models on first use. `audio-separate --list` shows the registry. Output dir produces one file per stem.
- Optional extra: install with `uv sync --extra separation` (pulls in torch + onnxruntime + librosa). Core install stays lean.
- `vidtools/separation.py` defines a `SeparationBackend` Protocol; the current `AudioSeparatorBackend` is backend-swappable so a future MLX-native implementation can replace the inference engine without breaking the CLI surface.
- Wired into `_OP_DISPATCH` as `audio-separate`.

## 0.8.1

### Changed (sanitize is now actually sanitizing)
- `sanitize_video` now strips **stream-level** metadata (`-map_metadata:s -1`) in addition to container metadata. The libx264/AAC encoder strings (which the encoders re-add post-encode) are explicitly cleared via `-metadata:s:v:0 encoder=` and `-metadata:s:a:0 encoder=`.
- `-fflags +bitexact` and `-flags:v +bitexact` suppress the libavformat/libavcodec version strings that previously leaked into the output container/stream tags.
- `audio_mode` default changed from `"copy"` to `"aac"`. The original audio bytes (and any audio fingerprint) are no longer preserved by default.
- Cropdetect now uses the full ffmpeg-returned `W:H:X:Y` so top letterbox is also removed, not just bottom (`extra_bottom` still applies to the detected height).
- CLI: `--aac` is gone (now the default). New `--keep-audio` opts back into stream copy. `--no-audio` unchanged. Help text rewritten to honestly describe what the operation does.

## 0.8.0

### Added
- **Operation pipelines**: chain multiple video operations into a single ffmpeg invocation. Faster than running ops sequentially with temp files; no quality loss from multiple re-encode passes. CLI: `vidtools pipeline in.mp4 out.mp4 cut --start 60 --end 90 resize -p 0.5 convert --crf 20`.
- Supported pipeline ops: cut, resize, crop, rotate, sanitize, subtitles, convert. Unsupported (different output type or multiple inputs): concat, slideshow, extract-audio/frames, add-audio, preset.
- `vidtools/pipeline.py`: `Pipeline` class with `build_command()` and `run()`, plus filter builder helpers.
- `_parse_pipeline_steps()` in cli.py: walks argv, splits on op keywords, parses each step with a per-op argparse.

### Validation
- Pipeline rejects `--copy` on convert step (filters require re-encoding).
- Pipeline rejects `sanitize` without `--manual-crop` (probe-based cropdetect not supported in single-pass mode).
- Pipeline rejects duplicate steps (at most one of each op type in v1).
- Smart input detection: pipeline only accepts video inputs (raises clearly for image/audio).

## 0.7.0

### Changed (architectural)
- **SQLite-backed state**: file registry and jobs now persist to `data/vidtools.db` (WAL mode, single connection, threading.Lock).
- File registry is no longer an in-memory dict serialized to JSON. Race conditions during concurrent uploads are eliminated.
- Jobs survive server restarts. On startup, any job left in RUNNING/QUEUED state is marked FAILED with `error="Server restart"`.
- One-time migration from legacy `file_registry.json` to SQLite on first startup. Old file is renamed to `.json.migrated`.

### Added
- `vidtools/db.py`: `Database`, `FileRegistry`, `JobStore`, `migrate_legacy_registry()`.
- `JobManager.load_from_store()` for crash recovery on startup.
- `JobStore.fail_unfinished()` for marking interrupted jobs as failed.

### Removed
- `_save_registry()`, `_load_registry()`, `_register_file()`, `_unregister_file()` from server.py (replaced by FileRegistry methods).
- `REGISTRY_FILE` constant (replaced by `DB_PATH`).

## 0.6.0

### Changed (architectural)
- **Thread-local progress sink** replaces `progress_callback` parameter on every operation. Operations no longer carry presentation concerns through their signatures.
- `run_ffmpeg_command` -> `run_ffmpeg`. Single mode of operation: always raises `FfmpegError` on failure (no more `exit(1)` paths). Progress flows through `vidtools.progress.report_progress()`.
- CLI installs a lazy tqdm sink in `handle_command` via `progress_context()`.
- Web server installs a WebSocket-broadcasting sink in `_run_and_broadcast`. `JobManager.run_job` work_fn signature simplified to `(job)` -- no more `(job, progress_callback)`.
- All 9 `_dispatch_*` functions in server.py drop their `cb` parameter.

### Added
- `vidtools/progress.py`: thread-local progress sink with `set_progress_sink()`, `report_progress()`, `progress_context()`, and `FfmpegError`.
- **Input-type smart dispatch**: `vidtools resize input.png` and `vidtools resize input.mp4` both work. Smart commands: `info`, `crop`, `resize`, `convert`, `cut`. They detect the input type by extension and route to the appropriate operation.

### Fixed
- All 3 `exit(1)` calls in `utils.py` removed -- the library no longer kills its host process.
- Error output is now properly captured (was already fixed in 0.5.0; reaffirmed in the new generator-free design).

### Removed
- `progress_callback` parameter from 73 call sites across operations, server, and CLI.
- Backwards-compatible `run_ffmpeg_command` alias.

## 0.5.0

### Added
- Image operations: resize, convert, crop, thumbnail, strip-exif, optimize, adjust, info
- Audio operations: convert, cut, normalize, mix, fade, resample, silence-detect, silence-remove, info
- CLI commands: img-resize, img-convert, img-crop, img-thumb, img-strip-exif, img-optimize, img-adjust, img-info
- CLI commands: audio-convert, audio-cut, audio-normalize, audio-fade, audio-resample, audio-info
- Hardware acceleration via --hwaccel flag (macOS VideoToolbox) for cut and convert
- HEVC/H.265 and AV1 codec presets (hevc_quality, av1_quality)
- ffmpeg version detection with minimum version check (5.1+)

### Fixed
- Preset application crash: key mismatch between preset dict and convert_format params
- concatenate_videos re-encode mode: `-c libx264` replaced with `-c:v libx264 -c:a aac`
- Error output lost in run_ffmpeg_command: stderr lines now accumulated during progress loop
- Deprecated `-async 1` replaced with `-af aresample=async=1`
- Subtitle path escaping: paths with colons/backslashes now work
- check_ffmpeg_installed returns False on CalledProcessError
- extract_frames validates fps > 0
- Invalid resize_mode in slideshow now raises ValueError

### Changed
- Split operations.py into video_ops.py, image_ops.py, audio_ops.py with facade re-export
- Eliminated main.py: handlers merged inline into cli.py
- Server dispatch refactored from 105-line if/elif to registry dict
- Preset loading uses mtime-based caching
- Codec defaults centralized (DEFAULT_VIDEO_CODEC, DEFAULT_AUDIO_CODEC)

### Removed
- Dead files: tui.py, tui.tcss, batch_convert.py, merge.sh, setup.py, test_sample.py
- Unused ImageFilter import

## 0.4.0

### Changed
- URL downloads now accept any HTTPS URL (not just YouTube) via yt-dlp's broad site support
- Renamed `validate_youtube_url` to `validate_download_url` with HTTPS-only validation
- Switched all remaining `json` usage to `orjson` (presets, operations, CLI info output)
- Web UI "YouTube" tab renamed to "Download" with generic labeling

### Removed
- Dead stub commands: merge, batch (convert/resize/cut/extract-audio), preset save
- Unused progress listener system from JobManager
- Unused `validate_path` function from security module
- Unused `crop_position` parameter from slideshow operation
- Dead re-exports and unused imports

### Added
- `.gitkeep` files for `data/` and `internal/` directory structure
- `_add_faststart_if_mp4` and `_add_time_range_args` helpers in operations.py

## 0.3.0

### Added
- `add-audio` operation: add audio tracks to video files (CLI, web API, and UI)
- Replace mode (default): replaces existing audio with new track
- Mix mode: layers new audio on top of existing audio using amix
- Volume control for the added audio track (0.0-10.0)

## 0.2.0

### Added
- Web-based UI accessible from any browser (mobile-first design)
- FastAPI server with REST API and WebSocket progress updates
- Password authentication with JWT sessions
- YouTube download support via yt-dlp (MP3, WAV, MP4)
- Job queue system with concurrent execution and real-time progress
- File upload/download management with auto-cleanup
- Security layer: bcrypt passwords, input validation, path traversal prevention, rate limiting
- systemd deployment with hardened service config
- Dark/light mode support (follows system preference)
- Setup script for Ubuntu server deployment

### Changed
- `run_ffmpeg_command` now accepts optional `progress_callback` for non-CLI usage
- Bumped Python requirement from 3.8 to 3.12
- Removed emojis from error output in utils.py

### Dependencies
- Added: fastapi, uvicorn, python-multipart, python-jose, bcrypt, yt-dlp, orjson

## 0.1.0

### Added
- Initial CLI with 14 FFmpeg operations
- Preset management system
- Optional TUI via Textual
- Structured logging with structlog
- Progress bars with tqdm
