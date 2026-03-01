"""Tests for vidtools.security module."""

import time
import tempfile
from pathlib import Path

import pytest

from vidtools.security import (
    hash_password,
    verify_password,
    create_token,
    verify_token,
    sanitize_filename,
    validate_path,
    validate_youtube_url,
    RateLimiter,
)


class TestPasswordHashing:
    def test_hash_returns_bytes_string(self):
        hashed = hash_password("testpassword")
        assert isinstance(hashed, str)
        assert hashed.startswith("$2")

    def test_verify_correct_password(self):
        hashed = hash_password("mypassword")
        assert verify_password("mypassword", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("mypassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_different_passwords_different_hashes(self):
        h1 = hash_password("password1")
        h2 = hash_password("password2")
        assert h1 != h2

    def test_same_password_different_hashes(self):
        """bcrypt uses random salt, so same password gives different hashes."""
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2
        assert verify_password("samepassword", h1) is True
        assert verify_password("samepassword", h2) is True


class TestJWT:
    def test_create_and_verify_token(self):
        token = create_token({"sub": "user1"})
        assert isinstance(token, str)
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user1"

    def test_expired_token(self):
        token = create_token({"sub": "user1"}, expires_minutes=-1)
        payload = verify_token(token)
        assert payload is None

    def test_invalid_token(self):
        payload = verify_token("not.a.valid.token")
        assert payload is None

    def test_tampered_token(self):
        token = create_token({"sub": "user1"})
        tampered = token[:-5] + "XXXXX"
        payload = verify_token(tampered)
        assert payload is None

    def test_token_contains_exp(self):
        token = create_token({"sub": "user1"})
        payload = verify_token(token)
        assert "exp" in payload


class TestSanitizeFilename:
    def test_normal_filename(self):
        assert sanitize_filename("video.mp4") == "video.mp4"

    def test_strips_path_separators(self):
        assert "/" not in sanitize_filename("../../etc/passwd")
        assert "\\" not in sanitize_filename("..\\..\\windows\\system32")

    def test_strips_special_chars(self):
        result = sanitize_filename('video<>:"|?*.mp4')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert "|" not in result
        assert "?" not in result
        assert "*" not in result

    def test_limits_length(self):
        long_name = "a" * 300 + ".mp4"
        result = sanitize_filename(long_name)
        assert len(result) <= 255

    def test_empty_filename(self):
        result = sanitize_filename("")
        assert result == "unnamed"

    def test_only_special_chars(self):
        result = sanitize_filename("...")
        assert result == "unnamed"

    def test_preserves_extension(self):
        result = sanitize_filename("my video file.mp4")
        assert result.endswith(".mp4")

    def test_null_bytes(self):
        result = sanitize_filename("file\x00name.mp4")
        assert "\x00" not in result


class TestValidatePath:
    def test_valid_path_within_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            allowed = Path(tmpdir)
            test_file = allowed / "test.mp4"
            test_file.touch()
            result = validate_path(str(test_file), allowed)
            assert result == test_file.resolve()

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            allowed = Path(tmpdir)
            with pytest.raises(ValueError, match="outside allowed"):
                validate_path("../../etc/passwd", allowed)

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            allowed = Path(tmpdir)
            link = allowed / "sneaky_link"
            link.symlink_to("/etc/passwd")
            with pytest.raises(ValueError, match="outside allowed"):
                validate_path(str(link), allowed)

    def test_absolute_path_outside_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            allowed = Path(tmpdir)
            with pytest.raises(ValueError, match="outside allowed"):
                validate_path("/etc/passwd", allowed)

    def test_relative_path_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            allowed = Path(tmpdir)
            subdir = allowed / "sub"
            subdir.mkdir()
            test_file = subdir / "test.mp4"
            test_file.touch()
            result = validate_path("sub/test.mp4", allowed)
            assert result == test_file.resolve()


class TestValidateYoutubeUrl:
    def test_valid_youtube_url(self):
        assert validate_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_valid_youtu_be_url(self):
        assert validate_youtube_url("https://youtu.be/dQw4w9WgXcQ") is True

    def test_valid_youtube_no_www(self):
        assert validate_youtube_url("https://youtube.com/watch?v=abc123") is True

    def test_valid_youtube_music(self):
        assert validate_youtube_url("https://music.youtube.com/watch?v=abc123") is True

    def test_invalid_domain(self):
        assert validate_youtube_url("https://evil.com/watch?v=abc123") is False

    def test_no_protocol(self):
        assert validate_youtube_url("youtube.com/watch?v=abc123") is False

    def test_empty_url(self):
        assert validate_youtube_url("") is False

    def test_command_injection(self):
        assert validate_youtube_url("https://youtube.com/watch?v=abc; rm -rf /") is False

    def test_http_url(self):
        assert validate_youtube_url("http://www.youtube.com/watch?v=abc123") is True

    def test_playlist_url(self):
        assert validate_youtube_url("https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf") is True


class TestRateLimiter:
    def test_allows_requests_under_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert limiter.is_allowed("192.168.1.1") is True

    def test_blocks_requests_over_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.is_allowed("192.168.1.1")
        assert limiter.is_allowed("192.168.1.1") is False

    def test_different_ips_independent(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.is_allowed("192.168.1.1")
        limiter.is_allowed("192.168.1.1")
        assert limiter.is_allowed("192.168.1.1") is False
        assert limiter.is_allowed("192.168.1.2") is True

    def test_window_expiry(self):
        limiter = RateLimiter(max_requests=1, window_seconds=1)
        limiter.is_allowed("192.168.1.1")
        assert limiter.is_allowed("192.168.1.1") is False
        time.sleep(1.1)
        assert limiter.is_allowed("192.168.1.1") is True
