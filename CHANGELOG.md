# Changelog

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
