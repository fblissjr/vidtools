"""install.sh itself, run for real against a temp Services directory.

Why this exists: `test_macos_workflow.py` asserts on dicts returned by pure
functions, so it passes whether or not installation works. The Quick Action's
fourth defect was exactly there — the bundle was overwritten in place, leaving
the bundle *directory's* mtime unchanged, so macOS kept its cached registration
and the action dropped out of System Settings while still working from the
right-click menu. No dict assertion can see that.

macOS-only; these shell out to the real script.
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS installer")

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "macos" / "install.sh"
NAME = "Compress Image"


def _install(services: Path):
    env = {**os.environ, "VIDTOOLS_SERVICES_DIR": str(services)}
    return subprocess.run(["bash", str(INSTALL)], capture_output=True, text=True, env=env)


@pytest.fixture
def services(tmp_path):
    d = tmp_path / "Services"
    d.mkdir()
    return d


def test_install_creates_a_complete_bundle(services):
    r = _install(services)
    assert r.returncode == 0, r.stderr
    contents = services / f"{NAME}.workflow" / "Contents"
    assert (contents / "document.wflow").exists()
    assert (contents / "Info.plist").exists()


def test_reinstall_replaces_the_bundle_rather_than_overwriting(services):
    """The regression proper. Rewriting files in place leaves the bundle
    directory's identity unchanged, which is what macOS keys its re-scan on."""
    _install(services)
    bundle = services / f"{NAME}.workflow"
    first_inode = bundle.stat().st_ino

    _install(services)
    assert bundle.stat().st_ino != first_inode, (
        "bundle directory reused across reinstall; macOS will keep its cached "
        "registration and the action silently drops out of Login Items & Extensions"
    )


def test_install_is_idempotent_in_content(services):
    """Replacing the bundle must not change what it contains."""
    _install(services)
    contents = services / f"{NAME}.workflow" / "Contents"
    first = {f.name: f.read_bytes() for f in contents.iterdir()}
    _install(services)
    second = {f.name: f.read_bytes() for f in contents.iterdir()}
    assert first == second


def test_installed_plists_parse_and_carry_the_registration_keys(services):
    _install(services)
    contents = services / f"{NAME}.workflow" / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    svc = info["NSServices"][0]
    for key in ("NSMenuItem", "NSMessage", "NSRequiredContext", "NSSendFileTypes"):
        assert key in svc, f"{key} missing; the action will not register"
    wf = plistlib.loads((contents / "document.wflow").read_bytes())
    assert wf["workflowMetaData"]["presentationMode"] == 15


def test_embedded_script_has_the_binary_substituted(services):
    _install(services)
    wf = plistlib.loads(
        (services / f"{NAME}.workflow" / "Contents" / "document.wflow").read_bytes())
    script = wf["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert "@VIDTOOLS_BIN@" not in script, "placeholder left unsubstituted"
    assert script.startswith("#!/bin/zsh")


def test_uninstall_removes_the_bundle(services):
    _install(services)
    bundle = services / f"{NAME}.workflow"
    assert bundle.exists()
    env = {**os.environ, "VIDTOOLS_SERVICES_DIR": str(services)}
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not bundle.exists()
