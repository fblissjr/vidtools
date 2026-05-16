# vidtools

Media tools wrapper with a web UI and CLI. Built because I always forget ffmpeg flags.

Handles video (FFmpeg), images (PIL), and audio (FFmpeg). Two ways to use it:

1. **Web UI** — mobile-first browser interface, designed so my wife can use it from her phone on our local network
2. **CLI** — for when I'm at a terminal and know what I want

## Quick start

Requires Python 3.12+, ffmpeg ≥ 5.1 and ffprobe in PATH. yt-dlp in PATH for URL downloads.

```bash
# Install
uv sync

# Start the web server (default: http://0.0.0.0:8910)
uv run uvicorn vidtools.server:app --host 0.0.0.0 --port 8910

# Or use the CLI directly
uv run vidtools <command> [options]
```

On first visit to the web UI, you'll set a password. After that, all access requires login. Files and jobs persist in SQLite (`data/vidtools.db`) and survive restarts.

## CLI

The CLI auto-detects input type from file extension for common operations. `vidtools resize file.mp4` uses ffmpeg; `vidtools resize file.png` uses PIL.

### Smart commands (auto-route by input type)

```bash
vidtools info file.{mp4,png,mp3}                    # video, image, or audio info
vidtools resize file.mp4 out.mp4 -p 0.5             # video resize
vidtools resize file.png out.png -p 0.5             # image resize
vidtools convert in.wav out.mp3                     # audio convert
vidtools convert in.avi out.mp4                     # video convert
vidtools crop in.png out.png -W 200 -H 200 -x 50 -y 50
vidtools cut in.mp4 out.mp4 --start 60 --end 90     # video cut
vidtools cut in.mp3 out.mp3 --start 0 --end 30      # audio cut
```

For full video-specific flags (`--algorithm`, `--vcodec`, `--accurate`, `--hwaccel`, etc.) use the explicit prefixes: `vidtools video-resize`, `video-convert`, `video-cut`. Image-only ops use `img-*`; audio-only use `audio-*`.

### Video commands

```bash
vidtools cut in.mp4 out.mp4 --start 00:00:40 --end 00:01:06.500
vidtools cut in.mp4 out.mp4 --start 40 --duration 26.5 --accurate
vidtools convert in.mp4 out.gif                     # GIF with palette generation
vidtools extract-audio in.mp4 out.mp3
vidtools extract-frames in.mp4 'frame_%04d.jpg' -r 1
vidtools concat a.mp4 b.mp4 c.mp4 -o combined.mp4
vidtools rotate in.mp4 out.mp4 -r 90
vidtools subtitles in.mp4 out.mp4 -s subs.srt
vidtools sanitize in.mp4 out.mp4                    # auto-crop, noise, strip metadata
vidtools slideshow 'images/*.png' out.mp4 --size 1920x1080 --resize-mode pad
vidtools add-audio video.mp4 music.mp3 out.mp4 --mix --volume 0.3
vidtools trim-to-audio in.mp4 out.mp4 --buffer 0.5   # match video duration to audio + buffer
vidtools srt-from-transcript transcript.txt out.srt
```

### Image commands (PIL)

```bash
vidtools img-resize in.png out.png -p 0.5
vidtools img-convert in.png out.webp -q 85
vidtools img-crop in.png out.png -W 500 -H 500 -x 100 -y 100
vidtools img-thumb in.png thumb.png -s 256
vidtools img-strip-exif in.jpg clean.jpg
vidtools img-optimize in.png small.jpg -q 80 --max-width 1920
vidtools img-adjust in.png out.png --brightness 1.2 --contrast 1.1 --rotation 90 --flip horizontal
vidtools img-info file.png
```

### Audio commands (FFmpeg)

```bash
vidtools audio-convert in.wav out.mp3 --bitrate 320k
vidtools audio-cut in.mp3 out.mp3 --start 60 --end 90
vidtools audio-normalize in.mp3 out.mp3 --target -16   # LUFS, two-pass loudnorm
vidtools audio-fade in.mp3 out.mp3 --fade-in 3 --fade-out 3 --total-duration 60
vidtools audio-resample in.mp3 out.mp3 -r 22050
vidtools audio-clean in.mp3 out.mp3                    # highpass + denoise + compress + loudnorm
vidtools audio-info file.mp3
```

### Audio source separation (optional)

Splits a track into stems (vocals/instrumental, or 4-stem) using mel-band roformer, BS-roformer, Demucs, MDX-Net, or VR via [`audio-separator`](https://github.com/nomadkaraoke/python-audio-separator). CoreML accelerated on Apple silicon.

```bash
uv sync --extra separation              # one-time install of torch + onnxruntime + librosa

vidtools audio-separate --list                                                   # list available models
vidtools audio-separate song.mp3 mel_band_roformer_kim_FT_by_aname-tommy.ckpt ./stems/
vidtools audio-separate song.mp3 htdemucs_ft.yaml ./stems/                       # 4-stem
```

Models auto-download to the audio-separator cache on first use. The backend is swappable (`vidtools.separation.SeparationBackend` Protocol) so a future MLX-native implementation can replace the inference engine without changing the CLI.

### Pipelines (chain video ops in one ffmpeg invocation)

```bash
vidtools pipeline in.mp4 out.mp4 \
    cut --start 60 --end 90 \
    resize -p 0.5 \
    convert --crf 20
```

3-5x faster than running separate commands; no intermediate files; no compounding quality loss. Supports cut, resize, crop, rotate, sanitize, subtitles, convert. Excludes multi-input or different-output ops (concat, slideshow, extract-*, add-audio, preset).

### Presets

```bash
vidtools preset list
vidtools preset apply in.mp4 out.mp4 compress_web
```

Built-in presets include `compress_web`, `audio_only_mp3`, `gif_optimized`, `resize_half`, `hq_h264_mp4`, `mobile_friendly_mp4`, `webm_social_media`, `hevc_quality` (libx265), `av1_quality` (libaom-av1).

### Hardware acceleration

```bash
vidtools convert in.mp4 out.mp4 --hwaccel        # macOS: uses VideoToolbox
vidtools cut in.mp4 out.mp4 --start 0 --end 60 --accurate --hwaccel
```

### Live broadcast recording (dl-live)

For shows that are only streamed live. Waits for the scheduled start, captures from second 0, survives network blips.

```bash
vidtools dl-live "https://www.youtube.com/watch?v=<id>" show.mp4
vidtools dl-live "https://www.youtube.com/@channel/live" capture.mp4 --from-now --no-wait
```

Output is mp4-only. See `CLAUDE.md` for the `ARG_MAX` reason and a known macOS-specific issue when the library is invoked via `subprocess.Popen` against an HLS-served stream — `examples/record_live.py` is a standalone yt-dlp wrapper that documents the same flag profile and surfaces the same issue. Until that's fixed, on macOS prefer invoking `yt-dlp` directly from the shell for live recordings:

```bash
uv --project /path/to/vidtools run yt-dlp \
  --no-playlist --newline --no-part \
  --retries infinite --fragment-retries infinite \
  -f 'bv*+ba/b' --remux-video mp4 \
  --live-from-start --wait-for-video 30 \
  -o "show.%(ext)s" \
  "<live broadcast url>"
```

## Web UI

The server exposes a mobile-first interface with three tabs:

- **External Download** — paste an HTTPS URL, pick MP3/WAV/MP4, download via yt-dlp
- **Process File** — upload a file, pick an operation, run it
- **My Files** — browse uploaded and output files, download or delete

Real-time progress via WebSocket. Jobs run in background workers (default 2 concurrent) and persist across server restarts. Interrupted jobs are auto-marked FAILED on next startup.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `VIDTOOLS_DATA_DIR` | `./data` | Where uploads, output, temp files, and SQLite DB live |
| `VIDTOOLS_CONFIG_DIR` | `~/.config/vidtools` | Password hash and config storage |
| `VIDTOOLS_MAX_UPLOAD_MB` | `2048` | Max upload size in MB |
| `VIDTOOLS_CLEANUP_HOURS` | `24` | Auto-delete output files after this many hours |

### API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | No | Serve web UI |
| GET | `/api/status` | No | Health check, auth status, version |
| POST | `/api/setup` | No | First-time password setup |
| POST | `/api/login` | Rate-limited | Authenticate, get session cookie |
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
| WS | `/ws/progress` | Cookie | Real-time job progress updates |

## Deployment

For running as a service on Ubuntu (e.g., a local server on your network):

```bash
cd deploy
sudo ./setup.sh
```

This creates a systemd service with security hardening (NoNewPrivileges, ProtectSystem=strict, PrivateTmp, etc.). See `deploy/vidtools.service` for the full unit file.

## Architecture

Operations are split by media type and re-exported from a thin facade:

- `video_ops.py` — FFmpeg video operations + shared filter helpers
- `image_ops.py` — PIL image operations
- `audio_ops.py` — FFmpeg audio operations
- `operations.py` — facade (`from .video_ops import *` etc.)

Cross-cutting infrastructure:

- `progress.py` — thread-local progress sink. Operations call `report_progress(percent, msg)`; CLI installs a tqdm sink, server installs a WebSocket-broadcasting sink. No callback parameters in operation signatures.
- `db.py` — SQLite at `data/vidtools.db` (WAL mode). `FileRegistry` and `JobStore`. One-time migration from legacy `file_registry.json` runs on first startup.
- `pipeline.py` — `Pipeline` class chains video ops into a single ffmpeg invocation with combined `-vf` filter chain.
- `cli.py` — argparse + smart input-type dispatch (`_make_smart`, `_detect_kind`).
- `server.py` — FastAPI + WebSocket. `_OP_DISPATCH` dict maps operation names to per-op handlers.
- `jobs.py` — `JobManager` with asyncio.Semaphore concurrency cap and SQLite-backed persistence.
- `security.py` — bcrypt + JWT + per-IP rate limiter.
- `ytdlp.py` — yt-dlp subprocess wrapper.

## Project structure

```
vidtools/
  __init__.py
  _version.py
  cli.py             # argparse + smart dispatch + pipeline argv parser
  operations.py      # facade re-exporting from video_ops/image_ops/audio_ops
  video_ops.py       # FFmpeg video ops + shared filter helpers
  image_ops.py       # PIL image ops
  audio_ops.py       # FFmpeg audio ops
  utils.py           # run_ffmpeg, ffmpeg version check, file validation
  progress.py        # thread-local progress sink + FfmpegError
  db.py              # SQLite-backed FileRegistry and JobStore
  pipeline.py        # Pipeline chaining (single ffmpeg invocation)
  jobs.py            # Job dataclass + JobManager (concurrency + persistence)
  server.py          # FastAPI server, REST API, WebSocket
  security.py        # bcrypt, JWT, rate limiting, URL validation
  ytdlp.py           # yt-dlp wrapper
  presets.py         # Preset management with mtime cache
  web/
    index.html       # Mobile-first UI, no build step
    style.css        # Dark/light theme support
    app.js           # Vanilla JS, manual DOM (no innerHTML)
deploy/
  setup.sh           # Ubuntu deployment script
  vidtools.service   # systemd unit file (hardened)
data/                # Uploads, output, temp, SQLite DB — gitignored except .gitkeep
tests/               # 11 files, ~176 tests (run: uv run pytest tests/ -v)
```

## Tests

```bash
uv run pytest tests/ -v
```

176 tests across security, jobs, operations, video/image/audio ops, ytdlp, server, dispatch, progress, db, and pipeline.
