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
    WebSocket,
    WebSocketDisconnect,
)
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import audio_ops, operations, separation
from ._version import __version__
from .db import Database, FileRegistry, JobStore, migrate_legacy_registry
from .jobs import Job, JobManager, JobStatus
from .security import (
    RateLimiter,
    create_token,
    hash_password,
    sanitize_filename,
    validate_download_url,
    verify_password,
    verify_token,
)
from .ytdlp import download as ytdlp_download
from .ytdlp import download_live as ytdlp_download_live
from .ytdlp import validate_download_params

logger = structlog.get_logger(__name__)

# --- Configuration ---

DATA_DIR = Path(os.environ.get("VIDTOOLS_DATA_DIR", "./data"))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
TEMP_DIR = DATA_DIR / "temp"
CONFIG_DIR = Path(os.environ.get("VIDTOOLS_CONFIG_DIR", os.path.expanduser("~/.config/vidtools")))
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_PATH = DATA_DIR / "vidtools.db"
LEGACY_REGISTRY_FILE = DATA_DIR / "file_registry.json"  # for migration only

MAX_UPLOAD_SIZE = int(os.environ.get("VIDTOOLS_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
FILE_CLEANUP_HOURS = int(os.environ.get("VIDTOOLS_CLEANUP_HOURS", "24"))

# --- Globals ---
# Initialized in lifespan startup (need DATA_DIR to exist first)

database: Database | None = None
file_registry: FileRegistry = None  # type: ignore[assignment]
job_store: JobStore | None = None
job_manager: JobManager = None  # type: ignore[assignment]
rate_limiter = RateLimiter(max_requests=60, window_seconds=60)
login_limiter = RateLimiter(max_requests=5, window_seconds=300)
ws_clients: list[WebSocket] = []


def _scan_directories_into_registry():
    """Scan upload/output directories and register any unknown files.
    Used when starting fresh (no DB) or after a manual file drop."""
    for directory in (UPLOAD_DIR, OUTPUT_DIR):
        if directory.exists():
            for f in directory.iterdir():
                if f.is_file():
                    fid = uuid.uuid4().hex[:12]
                    source = "upload" if directory == UPLOAD_DIR else "output"
                    file_registry.add(fid, f.name, str(f), f.stat().st_size, source)


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
            for directory in (UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR):
                if not directory.exists():
                    continue
                for f in directory.iterdir():
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        f.unlink()
                        logger.info("Cleaned up old file", path=str(f))

            file_registry.prune_missing()
            job_manager.cleanup_old_jobs(max_age_seconds=3600)
        except Exception:
            logger.exception("Error in cleanup loop")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    global database, file_registry, job_store, job_manager

    _ensure_dirs()

    # Set JWT secret from config
    from . import security
    security.SECRET_KEY = _get_secret_key()

    # Initialize SQLite-backed state
    database = Database(DB_PATH)
    file_registry = FileRegistry(database)
    job_store = JobStore(database)
    job_manager = JobManager(max_concurrent=2, store=job_store)

    # Migrate legacy JSON registry on first run
    migrate_legacy_registry(file_registry, LEGACY_REGISTRY_FILE)

    # Prune entries whose underlying files have disappeared, then scan for
    # any unknown files (e.g. dropped manually into the data directories)
    file_registry.prune_missing()
    if not file_registry.list_all():
        _scan_directories_into_registry()

    # Rehydrate jobs from disk and mark any still-running jobs as failed
    # (server crashed mid-execution last time)
    job_manager.load_from_store()

    cleanup_task = asyncio.create_task(_cleanup_loop())
    logger.info("Vidtools server started", data_dir=str(DATA_DIR), version=__version__)
    yield
    cleanup_task.cancel()
    if database is not None:
        database.close()
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

    file_registry.add(file_id, safe_name, str(dest_path), total_size, "upload")

    return {"id": file_id, "filename": safe_name, "size": total_size}


@app.get("/api/files", dependencies=[Depends(require_auth)])
async def list_files():
    """List all available files."""
    files = []
    for info in file_registry.list_all():
        if Path(info["path"]).exists():
            files.append({
                "id": info["id"],
                "filename": info["filename"],
                "size": info["size"],
                "created_at": info["created_at"],
                "source": info["source"],
            })
    return {"files": files}  # already sorted by created_at DESC


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
    file_registry.delete(file_id)
    return {"ok": True}


# --- Job endpoints ---

# --- Operation dispatch registry ---

def _dispatch_resize(input_path, output_path, params, **_):
    operations.resize_video(input_path, output_path, percentage=params.get("percentage"),
                            width=params.get("width"), height=params.get("height"),
                            algorithm=params.get("algorithm", "lanczos"))
    return output_path

def _dispatch_convert(input_path, output_path, params, **_):
    out_ext = params.get("format", Path(input_path).suffix.lstrip("."))
    operations.convert_format(input_path, output_path, format_type=out_ext,
                              video_codec=params.get("video_codec"), audio_codec=params.get("audio_codec"),
                              video_bitrate=params.get("video_bitrate"), audio_bitrate=params.get("audio_bitrate"),
                              quality_scale=params.get("crf"), preset=params.get("preset"),
                              use_copy=params.get("copy", False))
    return output_path

def _dispatch_cut(input_path, output_path, params, **_):
    operations.cut_video(input_path, output_path, start_time=params.get("start"),
                         end_time=params.get("end"), duration=params.get("duration"),
                         use_copy=params.get("copy", True))
    return output_path

def _dispatch_extract_audio(input_path, output_path, params, **ctx):
    out_ext = params.get("format", "mp3")
    input_name = Path(input_path).stem
    safe_out = sanitize_filename(f"{input_name}.{out_ext}")
    output_path = str(OUTPUT_DIR / f"{ctx['output_id']}_{safe_out}")
    operations.extract_audio(input_path, output_path,
                             audio_format=params.get("audio_codec", "copy"))
    return output_path

_SCREENSHOT_PARAMS = ("timestamp", "quality", "accurate")


def _dispatch_screenshot(input_path, output_path, params, **ctx):
    """Grab a single still frame. Output is an image; its extension is the
    requested `format` (default png), not the input's. Operation params are
    forwarded via _passthrough so screenshot()'s own defaults win."""
    out_ext = params.get("format", "png")
    input_name = Path(input_path).stem
    safe_out = sanitize_filename(f"{input_name}.{out_ext}")
    output_path = str(OUTPUT_DIR / f"{ctx['output_id']}_{safe_out}")
    operations.screenshot(input_path, output_path,
                          **_passthrough(params, _SCREENSHOT_PARAMS))
    return output_path

def _dispatch_crop(input_path, output_path, params, **_):
    operations.crop_video(input_path, output_path, width=params["width"], height=params["height"],
                          x=params.get("x", 0), y=params.get("y", 0))
    return output_path

def _dispatch_rotate(input_path, output_path, params, **_):
    operations.rotate_video(input_path, output_path,
                            rotation=str(params.get("rotation", "90")))
    return output_path

def _dispatch_concat(input_path, output_path, params, **_):
    input_ids = params.get("input_file_ids", [])
    input_paths = []
    for fid in input_ids:
        fi = file_registry.get(fid)
        if fi is None:
            raise RuntimeError(f"Input file not found: {fid}")
        input_paths.append(fi["path"])
    input_paths.insert(0, input_path)
    operations.concatenate_videos(input_paths, output_path,
                                  use_copy=params.get("copy", True))
    return output_path

def _dispatch_add_audio(input_path, output_path, params, **_):
    audio_info = file_registry.get(params.get("audio_file_id", ""))
    if audio_info is None:
        raise RuntimeError("Audio file not found")
    operations.add_audio(input_path, audio_info["path"], output_path,
                         mode=params.get("mode", "replace"), volume=params.get("volume", 1.0))
    return output_path

def _dispatch_preset(input_path, output_path, params, **_):
    operations.apply_preset(input_path, output_path, params.get("preset_name", ""))
    return output_path

_CLEAN_AUDIO_PARAMS = (
    "highpass", "denoise", "compress", "compress_threshold", "compress_ratio",
    "compress_attack", "compress_release", "loudnorm", "target_lufs",
    "true_peak", "lra", "audio_codec", "bitrate",
)
_TRIM_TO_AUDIO_PARAMS = (
    "buffer", "accurate", "video_codec", "audio_codec", "crf", "hwaccel",
)


def _passthrough(params: dict, keys) -> dict:
    """Return only the keys present in params, so the callee's defaults win."""
    return {k: params[k] for k in keys if k in params}


def _dispatch_audio_separate(input_path, output_path, params, **ctx):
    """Separate input into stems. `output_path` is treated as a directory hint;
    stems land in `<output_path without suffix>/`. The primary stem is returned
    as the job's output_file -- additional stems exist on disk in the same dir
    but aren't auto-registered (single-output assumption in `_run_processing_job`).
    """
    out_dir = str(Path(output_path).with_suffix("")) if Path(output_path).suffix else output_path
    model = params.get("model")
    if not model:
        raise RuntimeError("audio-separate requires 'model' param")
    outputs = separation.separate_audio(input_path, model, out_dir)
    if not outputs:
        raise RuntimeError("separation produced no output stems")
    return outputs[0]


def _dispatch_clean_audio(input_path, output_path, params, **_):
    audio_ops.clean_audio(input_path, output_path, **_passthrough(params, _CLEAN_AUDIO_PARAMS))
    return output_path


def _dispatch_trim_to_audio(input_path, output_path, params, **_):
    operations.trim_to_audio(input_path, output_path, **_passthrough(params, _TRIM_TO_AUDIO_PARAMS))
    return output_path

_OP_DISPATCH = {
    "resize": _dispatch_resize,
    "convert": _dispatch_convert,
    "cut": _dispatch_cut,
    "extract-audio": _dispatch_extract_audio,
    "screenshot": _dispatch_screenshot,
    "crop": _dispatch_crop,
    "rotate": _dispatch_rotate,
    "concat": _dispatch_concat,
    "add-audio": _dispatch_add_audio,
    "preset": _dispatch_preset,
    "trim-to-audio": _dispatch_trim_to_audio,
    "audio-clean": _dispatch_clean_audio,
    "audio-separate": _dispatch_audio_separate,
}


def _run_processing_job(job: Job):
    """Execute a processing operation for a job. Progress flows via the
    thread-local progress sink set by _run_and_broadcast."""
    input_info = file_registry.get(job.input_file)
    if input_info is None:
        raise RuntimeError(f"Input file not found: {job.input_file}")
    input_path = input_info["path"]

    params = job.params
    input_name = Path(input_path).stem
    out_ext = params.get("format", Path(input_path).suffix.lstrip("."))
    safe_out = sanitize_filename(f"{input_name}_{job.operation}.{out_ext}")
    output_id = uuid.uuid4().hex[:12]
    output_path = str(OUTPUT_DIR / f"{output_id}_{safe_out}")

    handler = _OP_DISPATCH.get(job.operation)
    if handler is None:
        raise RuntimeError(f"Unknown operation: {job.operation}")

    output_path = handler(input_path, output_path, params, output_id=output_id)

    out_path_obj = Path(output_path)
    if out_path_obj.exists():
        final_name = Path(output_path).name
        file_registry.add(output_id, final_name, output_path, out_path_obj.stat().st_size, "output")
        job.output_file = output_id


def _register_yt_output(
    job: Job,
    output_id: str,
    safe_name: str,
    actual_path: str,
    output_path: str,
    op_label: str,
) -> None:
    """Resolve the on-disk output file for a yt-dlp job and register it.

    yt-dlp's printed path is the source of truth; we fall back to the requested
    path, and finally scan OUTPUT_DIR by the output_id prefix in case yt-dlp
    silently changed the extension.
    """
    actual = Path(actual_path)
    if not actual.exists():
        actual = Path(output_path)
    if not actual.exists():
        stem = safe_name.rsplit(".", 1)[0]
        for candidate in OUTPUT_DIR.iterdir():
            if candidate.is_file() and candidate.name.startswith(stem):
                actual = candidate
                break

    if not actual.exists():
        raise RuntimeError(f"{op_label} completed but output file not found")

    file_registry.add(output_id, actual.name, str(actual), actual.stat().st_size, "output")
    job.output_file = output_id


def _run_ytdlp_job(job: Job):
    """Execute a yt-dlp download for a job."""
    url = job.url
    params = job.params
    format_type = params.get("format", "mp3")

    errors = validate_download_params(format_type)
    if errors:
        raise RuntimeError("; ".join(errors))

    output_id = uuid.uuid4().hex[:12]
    safe_name = sanitize_filename(f"download_{output_id}.{format_type}")
    output_path = str(OUTPUT_DIR / safe_name)

    actual_path = ytdlp_download(url=url, output_path=output_path, format_type=format_type)
    _register_yt_output(job, output_id, safe_name, actual_path, output_path, "Download")


def _run_ytdlp_live_job(job: Job):
    """Execute a yt-dlp live broadcast recording for a job."""
    url = job.url
    params = job.params

    output_id = uuid.uuid4().hex[:12]
    safe_name = sanitize_filename(f"live_{output_id}.mp4")
    output_path = str(OUTPUT_DIR / safe_name)

    actual_path = ytdlp_download_live(
        url=url,
        output_path=output_path,
        wait_interval=int(params.get("wait_interval", 60)),
        from_start=bool(params.get("from_start", True)),
    )
    _register_yt_output(job, output_id, safe_name, actual_path, output_path, "Live recording")


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
        if not validate_download_url(url):
            raise HTTPException(status_code=400, detail="Invalid download URL")
        job = job_manager.create_job(op, params, url=url)
        work_fn = _run_ytdlp_job
    elif op == "ytdlp-live":
        url = body.get("url", "")
        if not validate_download_url(url):
            raise HTTPException(status_code=400, detail="Invalid download URL")
        job = job_manager.create_job(op, params, url=url)
        work_fn = _run_ytdlp_live_job
    else:
        input_file_id = body.get("input_file_id")
        if not input_file_id or input_file_id not in file_registry:
            raise HTTPException(status_code=400, detail="Invalid input_file_id")
        job = job_manager.create_job(op, params, input_file=input_file_id)
        work_fn = _run_processing_job

    # Start job in background
    asyncio.create_task(_run_and_broadcast(job, work_fn))

    return job.to_dict()


async def _run_and_broadcast(job: Job, work_fn):
    """Run a job and broadcast progress to WebSocket clients.

    Sets a thread-local progress sink that updates the job and schedules
    a WebSocket broadcast back on the event loop.
    """
    from .progress import set_progress_sink
    loop = asyncio.get_running_loop()

    def sink(percent, message=""):
        job.update_progress(percent, message)
        loop.call_soon_threadsafe(
            lambda: loop.create_task(_broadcast_job_update(job))
        )

    # Wrap work_fn to install the sink in the worker thread
    def threaded_work(j):
        set_progress_sink(sink)
        try:
            work_fn(j)
        finally:
            set_progress_sink(None)

    await job_manager.run_job(job, threaded_work)
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
