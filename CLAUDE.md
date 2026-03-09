# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Code and Writing Style Guidelines

- Always use `uv`

## FFmpeg Usage

Ensure you always work with the latest ffmpeg documentation and features.

## Project Overview

vidtools is an FFmpeg + yt-dlp wrapper with two interfaces:

1. **Web UI** -- FastAPI server with mobile-first HTML/CSS/JS frontend, designed for phone access on a local network
2. **CLI** -- argparse-based command-line interface for direct terminal usage

Both interfaces share the same core operations in `operations.py`. The web server adds authentication, a job queue, file management, and WebSocket progress updates.

## Architecture

### Core Components

- **operations.py**: All FFmpeg operations (cut, resize, convert, extract, crop, rotate, concat, subtitles, sanitize, slideshow, preset apply, video info). Every function accepts an optional `progress_callback: Callable | None` parameter. When present, progress is reported via the callback instead of tqdm. Functions raise `ValueError`/`RuntimeError` on failure instead of printing or calling `sys.exit()`.

- **utils.py**: `run_ffmpeg_command()` executes ffmpeg subprocesses. Accepts optional `progress_callback` -- when set, calls `progress_callback(percent, message)` and raises `RuntimeError` on failure. When unset, uses tqdm progress bars and `exit(1)` on failure (CLI mode). Also contains `setup_logging()` and file validation helpers.

- **cli.py**: Argument parser setup using argparse. Defines all available commands and their parameters. Entry point for command-line usage via `vidtools` or `vt` console scripts.

- **main.py**: CLI handler functions. Each follows the pattern `<operation>_handler(args)` which unpacks argparse args and calls the corresponding function in `operations.py`.

- **server.py**: FastAPI application with REST API and WebSocket. Handles authentication (bcrypt + JWT), file upload/download, job creation and tracking, and real-time progress via WebSocket. File registry is persisted to `data/file_registry.json`. Jobs run in worker threads via `asyncio.to_thread` with thread-safe broadcast using `loop.call_soon_threadsafe`.

- **security.py**: Password hashing (bcrypt), JWT token creation/verification (python-jose), input sanitization (filename, download URL validation), and per-IP rate limiting.

- **jobs.py**: `Job` dataclass with status tracking and `JobManager` with `asyncio.Semaphore`-based concurrency limiting (default 2). Jobs are in-memory only (no database).

- **ytdlp.py**: yt-dlp subprocess wrapper. Uses `--print after_move:filepath` for reliable output filename detection. Parses progress from stdout. Supports MP3, WAV, MP4 formats with optional max duration limit.

- **presets.py**: Preset management system for saving and loading FFmpeg configurations. Stores presets in `presets/ffmpeg_presets.json`.

- **web/**: Vanilla HTML/CSS/JS frontend. No build step, no framework. Mobile-first layout with dark/light theme support. Safe DOM construction (no innerHTML). WebSocket reconnect with exponential backoff.

### Key Patterns

1. **Command Flow (CLI)**: CLI args -> Handler Function -> Core Operation -> `run_ffmpeg_command()`
2. **Command Flow (Web)**: HTTP POST /api/jobs -> `_run_ffmpeg_job()` -> Core Operation with progress_callback -> WebSocket broadcast
3. **Error Handling**: Operations raise exceptions (not print+exit). Server catches and sets job status to FAILED. CLI handlers print errors.
4. **Progress Tracking**: `progress_callback(percent, message)` for web mode, tqdm for CLI mode
5. **Thread Safety**: FFmpeg/yt-dlp run in worker threads via `asyncio.to_thread`. WebSocket broadcasts use `loop.call_soon_threadsafe`.
6. **Authentication**: bcrypt password hash stored in `~/.config/vidtools/config.json`, JWT session tokens in httponly cookies
7. **File Registry**: In-memory dict persisted to JSON on every mutation (register/unregister). Pruned on startup for missing files.

## Common Development Tasks

### Running the CLI

```bash
# Direct usage
uv run vidtools <command> [options]

# Examples
vidtools cut input.mp4 output.mp4 --start 00:00:40 --end 00:01:06.500
vidtools resize input.mp4 output.mp4 -p 0.5
vidtools convert input.avi output.mp4
vidtools info input.mp4
vidtools preset apply input.mp4 output.mp4 compress_web
```

### Running the Web Server

```bash
uv run uvicorn vidtools.server:app --host 0.0.0.0 --port 8910
```

### Running Tests

```bash
uv run pytest tests/ -v
```

95 tests across 5 test files (security, jobs, operations, ytdlp, server).

### Adding New Operations

1. Add the core function in `operations.py` with `progress_callback: Callable | None = None` parameter
2. Forward `progress_callback` to `run_ffmpeg_command()`
3. Raise `ValueError`/`RuntimeError` on failure (no `sys.exit()`, no `print()` for errors)
4. Add `-y` flag to ffmpeg commands (prevents interactive prompts in web mode)
5. Add command definition in `cli.py:setup_argparse()`
6. Create handler function in `main.py` following pattern: `<operation>_handler(args)`
7. Add the operation to `_run_ffmpeg_job()` in `server.py`
8. Write tests

## Dependencies

- Python >=3.12
- ffmpeg and ffprobe (must be in PATH)
- yt-dlp (must be in PATH, for URL downloads)
- Python packages: see `pyproject.toml` -- key ones are fastapi, uvicorn, bcrypt, python-jose, yt-dlp, orjson, tqdm, structlog
- Optional: textual (for TUI)

## Deployment

`deploy/setup.sh` sets up a systemd service on Ubuntu. `deploy/vidtools.service` is a hardened unit file.

## FFmpeg Best Practices Implemented

1. **Stream Copy (-c copy)**: Used by default for cut/concat to avoid quality loss
2. **Frame-Accurate Cuts**: --accurate flag enables re-encoding for precise cuts
3. **Fast Start**: Automatically adds `-movflags +faststart` for MP4
4. **Aspect Ratio Preservation**: Uses `-2` instead of `-1` for even dimensions in resize
5. **Optimized GIF Creation**: Uses palette generation for better quality
6. **Codec Auto-Detection**: Convert auto-detects format from file extension
7. **Overwrite Flag**: All web-mode commands include `-y` to prevent interactive stalls

## Important Notes

- All file paths in the code use absolute paths
- FFmpeg commands are executed via subprocess with proper error handling
- Preset modifications are immediately saved to disk
- Jobs are in-memory only; they don't survive server restarts
- File registry persists to disk and is pruned on startup
- Output files are auto-cleaned after VIDTOOLS_CLEANUP_HOURS (default 24)
