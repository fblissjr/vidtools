# Changelog

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
