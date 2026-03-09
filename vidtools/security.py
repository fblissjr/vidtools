"""Security utilities for vidtools web server.

Password hashing, JWT tokens, input validation, rate limiting.
"""

import re
import time
from pathlib import Path

import bcrypt
from jose import jwt, JWTError

# Secret key for JWT signing. In production, load from config file.
# This gets set by the server on startup from the config.
SECRET_KEY = "vidtools-default-key-change-me"
JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRY_MINUTES = 1440  # 24 hours


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt. Returns the hash as a string."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(plain.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_token(data: dict, expires_minutes: int | None = None) -> str:
    """Create a signed JWT token with expiry."""
    if expires_minutes is None:
        expires_minutes = DEFAULT_TOKEN_EXPIRY_MINUTES
    payload = data.copy()
    payload["exp"] = time.time() + (expires_minutes * 60)
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """Verify and decode a JWT token. Returns payload dict or None if invalid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None
    except Exception:
        return None


def sanitize_filename(name: str) -> str:
    """Sanitize a filename: strip dangerous chars, limit length, ensure safe."""
    if not name:
        return "unnamed"

    # Remove null bytes
    name = name.replace("\x00", "")

    # Take only the basename (strip any path components)
    name = name.replace("\\", "/")
    if "/" in name:
        name = name.rsplit("/", 1)[-1]

    # Remove dangerous characters
    name = re.sub(r'[<>:"|?*]', "", name)

    # Remove leading dots (hidden files / traversal)
    name = name.lstrip(".")

    if not name:
        return "unnamed"

    # Limit length while preserving extension
    if len(name) > 255:
        stem = Path(name).stem
        suffix = Path(name).suffix
        max_stem = 255 - len(suffix)
        name = stem[:max_stem] + suffix

    return name



# Download URL validation
_DOWNLOAD_URL_PATTERN = re.compile(
    r"^https://"
    r"[a-zA-Z0-9\-.]+"
    r"(/[a-zA-Z0-9_\-?&=%./]*)?"
    r"$"
)


def validate_download_url(url: str) -> bool:
    """Check that a URL is a valid HTTPS URL safe for download.

    Rejects empty strings, non-HTTPS schemes, and URLs with shell-injection characters.
    """
    if not url:
        return False
    return _DOWNLOAD_URL_PATTERN.match(url) is not None


class RateLimiter:
    """Simple in-memory per-IP rate limiter using sliding window."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}

    def is_allowed(self, ip: str) -> bool:
        """Check if a request from this IP is allowed."""
        now = time.time()
        cutoff = now - self.window_seconds

        if ip not in self._requests:
            self._requests[ip] = []

        # Remove expired entries
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

        if len(self._requests[ip]) >= self.max_requests:
            return False

        self._requests[ip].append(now)
        return True
