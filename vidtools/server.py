"""FastAPI web server for vidtools.

Provides REST API, WebSocket progress, file management, and authentication.
"""

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import orjson
import structlog
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import operations
from ._version import __version__
from .jobs import Job, JobManager, JobStatus
from .security import (
    RateLimiter,
    create_token,
    hash_password,
    sanitize_filename,
    validate_youtube_url,
    verify_password,
    verify_token,
)
from .ytdlp import download as ytdlp_download
from .ytdlp import validate_download_params

logger = structlog.get_logger(__name__)

# --- Configuration ---

DATA_DIR = Path(os.environ.get("VIDTOOLS_DATA_DIR", "./data"))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
TEMP_DIR = DATA_DIR / "temp"
CONFIG_DIR = Path(os.environ.get("VIDTOOLS_CONFIG_DIR", os.path.expanduser("~/.config/vidtools")))
CONFIG_FILE = CONFIG_DIR / "config.json"
REGISTRY_FILE = DATA_DIR / "file_registry.json"

MAX_UPLOAD_SIZE = int(os.environ.get("VIDTOOLS_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
FILE_CLEANUP_HOURS = int(os.environ.get("VIDTOOLS_CLEANUP_HOURS", "24"))

# --- Globals ---

job_manager = JobManager(max_concurrent=2)
rate_limiter = RateLimiter(max_requests=60, window_seconds=60)
login_limiter = RateLimiter(max_requests=5, window_seconds=300)
ws_clients: list[WebSocket] = []

# Track file metadata: file_id -> {filename, path, size, created_at, source}
file_registry: dict[str, dict] = {}


# --- File registry persistence ---

def _save_registry():
    """Persist file registry to disk."""
    try:
        REGISTRY_FILE.write_bytes(orjson.dumps(file_registry, option=orjson.OPT_INDENT_2))
    except Exception:
        logger.exception("Failed to save file registry")


def _load_registry():
    """Load file registry from disk. Prunes entries for files that no longer exist."""
    if REGISTRY_FILE.exists():
        try:
            loaded = orjson.loads(REGISTRY_FILE.read_bytes())
            # Prune entries whose files are gone, mutate in-place to keep references valid
            pruned = {
                fid: info for fid, info in loaded.items()
                if Path(info["path"]).exists()
            }
            file_registry.clear()
            file_registry.update(pruned)
            return
        except Exception:
            logger.exception("Failed to load file registry, scanning directories")

    # Fallback: scan directories
    for directory in (UPLOAD_DIR, OUTPUT_DIR):
        if directory.exists():
            for f in directory.iterdir():
                if f.is_file():
                    fid = uuid.uuid4().hex[:12]
                    file_registry[fid] = {
                        "filename": f.name,
                        "path": str(f),
                        "size": f.stat().st_size,
                        "created_at": f.stat().st_mtime,
                        "source": "upload" if directory == UPLOAD_DIR else "output",
                    }
    _save_registry()


def _register_file(file_id: str, filename: str, path: str, size: int, source: str):
    """Register a file and persist to disk."""
    file_registry[file_id] = {
        "filename": filename,
        "path": path,
        "size": size,
        "created_at": time.time(),
        "source": source,
    }
    _save_registry()


def _unregister_file(file_id: str):
    """Remove a file from registry and persist."""
    file_registry.pop(file_id, None)
    _save_registry()


# --- Config helpers ---

def _ensure_dirs():
    """Create data directories if they don't exist."""
    for d in (UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _load_config() -> dict:
    """Load config from disk."""
    if CONFIG_FILE.exists():
        return orjson.loads(CONFIG_FILE.read_bytes())
    return {}


def _save_config(config: dict):
    """Save config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_bytes(orjson.dumps(config, option=orjson.OPT_INDENT_2))


def _get_password_hash() -> str | None:
    """Get the stored password hash, or None if not set up."""
    config = _load_config()
    return config.get("password_hash")


def _set_password_hash(password: str):
    """Set the password hash in config."""
    config = _load_config()
    config["password_hash"] = hash_password(password)
    config["secret_key"] = uuid.uuid4().hex
    _save_config(config)


def _get_secret_key() -> str:
    """Get or create the JWT secret key."""
    config = _load_config()
    if "secret_key" not in config:
        config["secret_key"] = uuid.uuid4().hex
        _save_config(config)
    return config["secret_key"]


# --- Lifespan ---

async def _cleanup_loop():
    """Background task to clean up old files and jobs."""
    while True:
        await asyncio.sleep(3600)  # Run every hour
        try:
            cutoff = time.time() - (FILE_CLEANUP_HOURS * 3600)
            removed_any = False
            for directory in (UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR):
                if not directory.exists():
                    continue
                for f in directory.iterdir():
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        f.unlink()
                        logger.info("Cleaned up old file", path=str(f))
                        removed_any = True

            # Clean up file registry entries for deleted files
            to_remove = [
                fid for fid, info in file_registry.items()
                if not Path(info["path"]).exists()
            ]
            for fid in to_remove:
                del file_registry[fid]
                removed_any = True

            if removed_any:
                _save_registry()

            job_manager.cleanup_old_jobs(max_age_seconds=3600)
        except Exception:
            logger.exception("Error in cleanup loop")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    _ensure_dirs()

    # Set JWT secret from config
    from . import security
    security.SECRET_KEY = _get_secret_key()

    # Load persisted file registry (or scan directories as fallback)
    _load_registry()

    cleanup_task = asyncio.create_task(_cleanup_loop())
    logger.info("Vidtools server started", data_dir=str(DATA_DIR), version=__version__)
    yield
    cleanup_task.cancel()
    _save_registry()
    logger.info("Vidtools server stopped")


# --- App ---

app = FastAPI(title="vidtools", lifespan=lifespan)

# Mount static files for the web UI
_web_dir = Path(__file__).parent / "web"
if _web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_web_dir)), name="static")


# --- Auth dependency ---

def _get_client_ip(request: Request) -> str:
    """Get client IP, respecting X-Forwarded-For for reverse proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def require_auth(request: Request, session: str | None = Cookie(default=None)):
    """Dependency that requires valid auth. Raises 401 if not authenticated."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(session)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return payload


# --- Routes ---

@app.get("/")
async def index():
    """Serve the main UI page."""
    index_path = _web_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return JSONResponse({"error": "Web UI not found"}, status_code=404)


@app.get("/api/status")
async def status(request: Request, session: str | None = Cookie(default=None)):
    """Server health check + auth status."""
    authed = False
    if session:
        payload = verify_token(session)
        authed = payload is not None

    needs_setup = _get_password_hash() is None
    return {
        "status": "ok",
        "authenticated": authed,
        "needs_setup": needs_setup,
        "version": __version__,
    }


@app.post("/api/setup")
async def setup(request: Request):
    """First-time password setup."""
    if _get_password_hash() is not None:
        raise HTTPException(status_code=400, detail="Password already configured")

    body = await request.json()
    password = body.get("password", "").strip()
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    _set_password_hash(password)

    # Update secret key in security module
    from . import security
    security.SECRET_KEY = _get_secret_key()

    # Auto-login after setup
    token = create_token({"sub": "admin"})
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "session", token, httponly=True, samesite="strict", max_age=86400
    )
    return response


@app.post("/api/login")
async def login(request: Request):
    """Authenticate and return session cookie."""
    ip = _get_client_ip(request)
    if not login_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    body = await request.json()
    password = body.get("password", "")

    stored_hash = _get_password_hash()
    if stored_hash is None:
        raise HTTPException(status_code=400, detail="Password not configured. Run setup first.")

    if not verify_password(password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = create_token({"sub": "admin"})
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "session", token, httponly=True, samesite="strict", max_age=86400
    )
    return response


@app.post("/api/logout")
async def logout():
    """Clear session cookie."""
    response = JSONResponse({"ok": True})
    response.delete_cookie("session")
    return response


# --- File endpoints ---

@app.post("/api/upload", dependencies=[Depends(require_auth)])
async def upload_file(request: Request):
    """Upload a file. Uses streaming to handle large files."""
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=400, detail="Expected multipart/form-data")

    form = await request.form()
    uploaded = form.get("file")
    if uploaded is None or not isinstance(uploaded, UploadFile):
        raise HTTPException(status_code=400, detail="No file provided")

    safe_name = sanitize_filename(uploaded.filename or "upload")
    file_id = uuid.uuid4().hex[:12]

    # Add file_id prefix to avoid collisions
    dest_name = f"{file_id}_{safe_name}"
    dest_path = UPLOAD_DIR / dest_name

    total_size = 0
    with open(dest_path, "wb") as f:
        while chunk := await uploaded.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE:
                f.close()
                dest_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File too large")
            f.write(chunk)

    _register_file(file_id, safe_name, str(dest_path), total_size, "upload")

    return {"id": file_id, "filename": safe_name, "size": total_size}


@app.get("/api/files", dependencies=[Depends(require_auth)])
async def list_files():
    """List all available files."""
    files = []
    for fid, info in file_registry.items():
        if Path(info["path"]).exists():
            files.append({
                "id": fid,
                "filename": info["filename"],
                "size": info["size"],
                "created_at": info["created_at"],
                "source": info["source"],
            })
    return {"files": sorted(files, key=lambda x: x["created_at"], reverse=True)}


@app.get("/api/files/{file_id}/download", dependencies=[Depends(require_auth)])
async def download_file(file_id: str):
    """Download a file by ID."""
    info = file_registry.get(file_id)
    if info is None or not Path(info["path"]).exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        info["path"],
        filename=info["filename"],
        media_type="application/octet-stream",
    )


@app.delete("/api/files/{file_id}", dependencies=[Depends(require_auth)])
async def delete_file(file_id: str):
    """Delete a file by ID."""
    info = file_registry.get(file_id)
    if info is None:
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(info["path"])
    if path.exists():
        path.unlink()
    _unregister_file(file_id)
    return {"ok": True}


# --- Job endpoints ---

def _run_ffmpeg_job(job: Job, progress_callback):
    """Execute an FFmpeg operation for a job."""
    input_info = file_registry.get(job.input_file)
    if input_info is None:
        raise RuntimeError(f"Input file not found: {job.input_file}")
    input_path = input_info["path"]

    op = job.operation
    params = job.params

    # Determine output filename
    input_name = Path(input_path).stem
    out_ext = params.get("format", Path(input_path).suffix.lstrip("."))
    out_name = f"{input_name}_{op}.{out_ext}"
    safe_out = sanitize_filename(out_name)
    output_id = uuid.uuid4().hex[:12]
    output_path = str(OUTPUT_DIR / f"{output_id}_{safe_out}")

    # Map operation to function, threading progress_callback through
    if op == "resize":
        operations.resize_video(
            input_path, output_path,
            percentage=params.get("percentage"),
            width=params.get("width"),
            height=params.get("height"),
            algorithm=params.get("algorithm", "lanczos"),
            progress_callback=progress_callback,
        )
    elif op == "convert":
        operations.convert_format(
            input_path, output_path,
            format_type=out_ext,
            video_codec=params.get("video_codec"),
            audio_codec=params.get("audio_codec"),
            video_bitrate=params.get("video_bitrate"),
            audio_bitrate=params.get("audio_bitrate"),
            quality_scale=params.get("crf"),
            preset=params.get("preset"),
            use_copy=params.get("copy", False),
            progress_callback=progress_callback,
        )
    elif op == "cut":
        operations.cut_video(
            input_path, output_path,
            start_time=params.get("start"),
            end_time=params.get("end"),
            duration=params.get("duration"),
            use_copy=params.get("copy", True),
            progress_callback=progress_callback,
        )
    elif op == "extract-audio":
        out_ext = params.get("format", "mp3")
        safe_out = sanitize_filename(f"{input_name}.{out_ext}")
        output_path = str(OUTPUT_DIR / f"{output_id}_{safe_out}")
        operations.extract_audio(
            input_path, output_path,
            audio_format=params.get("audio_codec", "copy"),
            progress_callback=progress_callback,
        )
    elif op == "crop":
        operations.crop_video(
            input_path, output_path,
            width=params["width"], height=params["height"],
            x=params.get("x", 0), y=params.get("y", 0),
            progress_callback=progress_callback,
        )
    elif op == "rotate":
        operations.rotate_video(
            input_path, output_path,
            rotation=str(params.get("rotation", "90")),
            progress_callback=progress_callback,
        )
    elif op == "concat":
        input_ids = params.get("input_file_ids", [])
        input_paths = []
        for fid in input_ids:
            fi = file_registry.get(fid)
            if fi is None:
                raise RuntimeError(f"Input file not found: {fid}")
            input_paths.append(fi["path"])
        input_paths.insert(0, input_path)
        operations.concatenate_videos(
            input_paths, output_path,
            use_copy=params.get("copy", True),
            progress_callback=progress_callback,
        )
    elif op == "add-audio":
        audio_info = file_registry.get(params.get("audio_file_id", ""))
        if audio_info is None:
            raise RuntimeError("Audio file not found")
        audio_path = audio_info["path"]
        operations.add_audio(
            input_path, audio_path, output_path,
            mode=params.get("mode", "replace"),
            volume=params.get("volume", 1.0),
            progress_callback=progress_callback,
        )
    elif op == "preset":
        operations.apply_preset(
            input_path, output_path,
            params.get("preset_name", ""),
            progress_callback=progress_callback,
        )
    else:
        raise RuntimeError(f"Unknown operation: {op}")

    # Register output file
    out_path_obj = Path(output_path)
    if out_path_obj.exists():
        _register_file(output_id, safe_out, output_path, out_path_obj.stat().st_size, "output")
        job.output_file = output_id


def _run_ytdlp_job(job: Job, progress_callback):
    """Execute a yt-dlp download for a job."""
    url = job.url
    params = job.params
    format_type = params.get("format", "mp3")

    errors = validate_download_params(format_type)
    if errors:
        raise RuntimeError("; ".join(errors))

    # Build output path using yt-dlp's template syntax
    output_id = uuid.uuid4().hex[:12]
    safe_name = sanitize_filename(f"download_{output_id}.{format_type}")
    output_path = str(OUTPUT_DIR / safe_name)

    actual_path = ytdlp_download(
        url=url,
        output_path=output_path,
        format_type=format_type,
        progress_callback=progress_callback,
    )

    # Find the actual output file -- yt-dlp may change the extension
    actual = Path(actual_path)
    if not actual.exists():
        actual = Path(output_path)
    if not actual.exists():
        # Scan for any file starting with our output_id prefix
        stem = safe_name.rsplit(".", 1)[0]
        for candidate in OUTPUT_DIR.iterdir():
            if candidate.is_file() and candidate.name.startswith(stem):
                actual = candidate
                break

    if actual.exists():
        _register_file(output_id, actual.name, str(actual), actual.stat().st_size, "output")
        job.output_file = output_id
    else:
        raise RuntimeError("Download completed but output file not found")


@app.post("/api/jobs", dependencies=[Depends(require_auth)])
async def create_job(request: Request):
    """Create a new processing job."""
    body = await request.json()
    op = body.get("operation")
    if not op:
        raise HTTPException(status_code=400, detail="Missing 'operation'")

    params = body.get("params", {})

    if op == "ytdlp":
        url = body.get("url", "")
        if not validate_youtube_url(url):
            raise HTTPException(status_code=400, detail="Invalid YouTube URL")
        job = job_manager.create_job(op, params, url=url)
        work_fn = _run_ytdlp_job
    else:
        input_file_id = body.get("input_file_id")
        if not input_file_id or input_file_id not in file_registry:
            raise HTTPException(status_code=400, detail="Invalid input_file_id")
        job = job_manager.create_job(op, params, input_file=input_file_id)
        work_fn = _run_ffmpeg_job

    # Start job in background
    asyncio.create_task(_run_and_broadcast(job, work_fn))

    return job.to_dict()


async def _run_and_broadcast(job: Job, work_fn):
    """Run a job and broadcast progress to WebSocket clients."""
    # Get reference to the running event loop for thread-safe scheduling
    loop = asyncio.get_running_loop()
    original_update = job.update_progress

    def _progress_with_broadcast(percent, message=""):
        original_update(percent, message)
        # Schedule broadcast from the worker thread back to the event loop
        loop.call_soon_threadsafe(
            lambda: loop.create_task(_broadcast_job_update(job))
        )

    job.update_progress = _progress_with_broadcast
    await job_manager.run_job(job, work_fn)
    await _broadcast_job_update(job)


async def _broadcast_job_update(job: Job):
    """Send job update to all connected WebSocket clients."""
    data = orjson.dumps({"type": "job_update", "job": job.to_dict()}).decode()
    disconnected = []
    for ws in ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        try:
            ws_clients.remove(ws)
        except ValueError:
            pass


@app.get("/api/jobs", dependencies=[Depends(require_auth)])
async def list_jobs():
    """List all jobs."""
    return {"jobs": [j.to_dict() for j in job_manager.list_jobs()]}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
async def get_job(job_id: str):
    """Get job details."""
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
async def cancel_job(job_id: str):
    """Cancel a running job."""
    if not job_manager.cancel_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found or not cancellable")
    return {"ok": True}


@app.get("/api/presets", dependencies=[Depends(require_auth)])
async def list_presets():
    """List available presets."""
    from .presets import get_presets
    return {"presets": get_presets()}


# --- WebSocket ---

@app.websocket("/ws/progress")
async def websocket_progress(ws: WebSocket, session: str | None = Cookie(default=None)):
    """WebSocket endpoint for real-time job progress updates."""
    # Verify auth
    if not session or verify_token(session) is None:
        await ws.close(code=4001, reason="Not authenticated")
        return

    await ws.accept()
    ws_clients.append(ws)
    try:
        # Send current job states on connect
        for job in job_manager.list_jobs():
            if job.status in (JobStatus.RUNNING, JobStatus.QUEUED):
                data = orjson.dumps({"type": "job_update", "job": job.to_dict()}).decode()
                await ws.send_text(data)

        # Keep connection alive, handle client messages
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30)
                if msg == "ping":
                    await ws.send_text(orjson.dumps({"type": "pong"}).decode())
            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await ws.send_text(orjson.dumps({"type": "heartbeat"}).decode())
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        try:
            ws_clients.remove(ws)
        except ValueError:
            pass
