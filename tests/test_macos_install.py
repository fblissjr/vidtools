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
import shutil
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


@pytest.fixture
def fake_repo(tmp_path):
    """A minimal repo copy, so a test can break the build without touching the
    real tree. install.sh derives REPO_ROOT from its own location, so a copied
    macos/ plus a stub .venv is a complete environment for it."""
    repo = tmp_path / "repo"
    (repo / "macos").mkdir(parents=True)
    for f in ("install.sh", "make_workflow.py", "compress-image.sh"):
        shutil.copy(REPO / "macos" / f, repo / "macos" / f)
    (repo / "macos" / "install.sh").chmod(0o755)

    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    vt = venv_bin / "vidtools"
    vt.write_text("#!/bin/sh\nexit 0\n")       # present *and* runnable
    vt.chmod(0o755)
    (venv_bin / "python3").symlink_to(sys.executable)
    return repo


def _install_from(repo: Path, services: Path, extra_env=None):
    env = {**os.environ, "VIDTOOLS_SERVICES_DIR": str(services), **(extra_env or {})}
    return subprocess.run(["bash", str(repo / "macos" / "install.sh")],
                          capture_output=True, text=True, env=env)


def test_install_rejects_a_binary_that_does_not_run(fake_repo, services):
    """Claim: deleting this lets the installer print "installed:" over a corpse.

    `-x` proves a file is executable, not that it imports. A venv whose package
    directory had gone missing passed that check, so the installer reported
    success and the failure surfaced later as an empty Automator dialog."""
    (fake_repo / ".venv" / "bin" / "vidtools").write_text(
        "#!/bin/sh\necho 'ModuleNotFoundError' >&2\nexit 1\n")
    (fake_repo / ".venv" / "bin" / "vidtools").chmod(0o755)

    r = _install_from(fake_repo, services)
    assert r.returncode != 0, "installer accepted a binary that cannot run"
    assert not (services / f"{NAME}.workflow").exists(), "broken bundle installed"


def test_failed_generation_leaves_the_previous_bundle_intact(fake_repo, services):
    """Claim: deleting this lets a mid-install failure destroy a working action.

    The old sequence was rm -rf, mkdir, then generate. Anything that failed
    during generation left no bundle at all and no way back."""
    assert _install_from(fake_repo, services).returncode == 0
    contents = services / f"{NAME}.workflow" / "Contents"
    before = {f.name: f.read_bytes() for f in contents.iterdir()}

    (fake_repo / "macos" / "make_workflow.py").write_text(
        "import sys\nsys.exit('generation blew up')\n")
    r = _install_from(fake_repo, services)

    assert r.returncode != 0, "install reported success despite a failed build"
    after = {f.name: f.read_bytes() for f in contents.iterdir()}
    assert after == before, "a failed install destroyed the working bundle"


def test_install_does_not_depend_on_python3_from_path(fake_repo, services, tmp_path):
    """Claim: deleting this lets the install bind to whatever python3 PATH
    happens to surface -- which on this machine is a user-local 3.14, not the
    venv's. The venv is already a hard precondition, so its python is the one
    guaranteed to exist."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "python3").write_text("#!/bin/sh\necho 'wrong python' >&2\nexit 1\n")
    (shadow / "python3").chmod(0o755)

    r = _install_from(fake_repo, services,
                      extra_env={"PATH": f"{shadow}:{os.environ['PATH']}"})
    assert r.returncode == 0, f"install used PATH python3: {r.stderr}"
    assert (services / f"{NAME}.workflow" / "Contents" / "document.wflow").exists()


def test_uninstall_removes_the_bundle(services):
    _install(services)
    bundle = services / f"{NAME}.workflow"
    assert bundle.exists()
    env = {**os.environ, "VIDTOOLS_SERVICES_DIR": str(services)}
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not bundle.exists()
