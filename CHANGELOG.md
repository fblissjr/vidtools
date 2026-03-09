# Changelog

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
