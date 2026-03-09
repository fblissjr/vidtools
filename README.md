# vidtools

FFmpeg + yt-dlp wrapper with a web UI and CLI. Built because I always forget ffmpeg flags.

Two ways to use it:
1. **Web UI** -- mobile-first browser interface, designed so my wife can use it from her phone on our local network
2. **CLI** -- for when I'm at a terminal and know what I want

## Quick start

Requires Python 3.12+, ffmpeg, and ffprobe in PATH.

```bash
# Install
uv sync

# Start the web server (default: http://0.0.0.0:8910)
uv run uvicorn vidtools.server:app --host 0.0.0.0 --port 8910

# Or use the CLI directly
uv run vidtools <command> [options]
```

On first visit to the web UI, you'll set a password. After that, all access requires login.

## Web UI

The server exposes a mobile-first interface with three tabs:

- **External Download** -- paste a URL, pick MP3/WAV/MP4, download
- **Process File** -- upload a video, pick an operation (cut, resize, convert, etc.), run it
- **My Files** -- browse uploaded and output files, download or delete them

Real-time progress via WebSocket. Jobs run in the background with a configurable concurrency limit (default 2).

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `VIDTOOLS_DATA_DIR` | `./data` | Where uploads, output, and temp files live |
| `VIDTOOLS_CONFIG_DIR` | `~/.config/vidtools` | Password hash and config storage |
| `VIDTOOLS_MAX_UPLOAD_MB` | `2048` | Max upload size in MB |
| `VIDTOOLS_CLEANUP_HOURS` | `24` | Auto-delete output files after this many hours |

### API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | No | Serve web UI |
| GET | `/api/status` | No | Health check, auth status, version |
| POST | `/api/setup` | No | First-time password setup |
| POST | `/api/login` | No | Authenticate, get session cookie |
| POST | `/api/logout` | Yes | Clear session |
| POST | `/api/upload` | Yes | Upload a file |
| GET | `/api/files` | Yes | List all files |
| GET | `/api/files/{id}/download` | Yes | Download a file |
| DELETE | `/api/files/{id}` | Yes | Delete a file |
| POST | `/api/jobs` | Yes | Create a job (ffmpeg op or yt-dlp download) |
| GET | `/api/jobs` | Yes | List all jobs |
| GET | `/api/jobs/{id}` | Yes | Get job status |
| DELETE | `/api/jobs/{id}` | Yes | Cancel a job |
| GET | `/api/presets` | Yes | List available presets |
| WS | `/ws/progress` | No | Real-time job progress updates |

## CLI commands

```bash
# Cut video (fast, stream copy)
vidtools cut input.mp4 output.mp4 --start 00:00:40 --end 00:01:06.500

# Cut with frame accuracy (re-encodes)
vidtools cut input.mp4 output.mp4 --start 40 --duration 26.5 --accurate

# Resize to 50%
vidtools resize input.mp4 output.mp4 -p 0.5

# Convert format (auto-detects from extension)
vidtools convert input.avi output.mp4

# Convert to optimized GIF
vidtools convert input.mp4 output.gif

# Extract audio
vidtools extract-audio input.mp4 output.mp3 --format mp3

# Concatenate videos
vidtools concat video1.mp4 video2.mp4 video3.mp4 -o combined.mp4

# Crop video
vidtools crop input.mp4 output.mp4 -w 640 -h 480 -x 100 -y 50

# Rotate video
vidtools rotate input.mp4 output.mp4 --angle 90

# Burn subtitles
vidtools subtitles input.mp4 output.mp4 --subtitle-file subs.srt

# Video info
vidtools info input.mp4

# Apply a preset
vidtools preset apply input.mp4 output.mp4 compress_web

# List presets
vidtools preset list
```

Full list: cut, resize, convert, extract-audio, extract-frames, concat, crop, rotate, subtitles, sanitize, slideshow, add-audio, srt-from-transcript, info, preset.

## Deployment

For running as a service on Ubuntu (e.g., a local server on your network):

```bash
cd deploy
sudo ./setup.sh
```

This creates a systemd service with security hardening (NoNewPrivileges, ProtectSystem, PrivateTmp). See `deploy/vidtools.service` for details.

## Project structure

```
vidtools/
  __init__.py        # Package exports
  _version.py        # Version
  cli.py             # Argparse CLI
  main.py            # CLI handler functions
  operations.py      # Core FFmpeg operations (all accept progress_callback)
  utils.py           # FFmpeg execution, logging, validation
  presets.py         # Preset management
  security.py        # Password hashing, JWT, input validation, rate limiting
  jobs.py            # Job queue with async execution
  server.py          # FastAPI server, REST API, WebSocket
  ytdlp.py           # yt-dlp wrapper
  web/
    index.html       # Mobile-first UI
    style.css        # Dark/light theme support
    app.js           # Vanilla JS, no build step
deploy/
  setup.sh           # Ubuntu deployment script
  vidtools.service   # systemd unit file
data/               # Runtime data (uploads, output, temp) -- gitignored except .gitkeep
internal/           # Internal logs -- gitignored except .gitkeep
tests/
  test_security.py
  test_jobs.py
  test_operations.py
  test_ytdlp.py
  test_server.py
```
