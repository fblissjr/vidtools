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
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS installer")

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "macos" / "install.sh"
NAME = "Compress Image"
VIDEO_NAME = "Compress Video"


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


# ------------------------------------------------------- both Quick Actions
#
# The video action is a second bundle, so everything the installer does it now
# has to do twice -- and the two are not symmetric: the video one additionally
# needs ffmpeg's directory baked in, and is the only one that can be skipped.
#
# These carry `ffmpeg` because the installer *deliberately* skips the video
# bundle when ffmpeg is absent, so asserting the bundle exists is asserting the
# toolchain exists. Without the marker they fail for an environment reason on
# any machine without ffmpeg -- exactly what the marker convention prevents.
# The one test that asserts the *skip* is correspondingly unmarked: it is the
# no-ffmpeg case, and it has to run precisely where these do not.

@pytest.mark.ffmpeg
def test_install_creates_both_bundles(services):
    r = _install(services)
    assert r.returncode == 0, r.stderr
    for name in (NAME, VIDEO_NAME):
        contents = services / f"{name}.workflow" / "Contents"
        assert (contents / "document.wflow").exists(), f"{name}: no workflow"
        assert (contents / "Info.plist").exists(), f"{name}: no Info.plist"


@pytest.mark.ffmpeg
def test_the_two_bundles_register_for_different_file_types(services):
    """Claim: deleting this lets both bundles register for public.image.

    The video action would then offer video presets when right-clicking a photo
    and never appear on an actual video -- installed, lint-clean, and useless."""
    _install(services)
    kinds = {}
    for name in (NAME, VIDEO_NAME):
        info = plistlib.loads(
            (services / f"{name}.workflow" / "Contents" / "Info.plist").read_bytes())
        kinds[name] = info["NSServices"][0]["NSSendFileTypes"]
    assert kinds[NAME] == ["public.image"]
    assert kinds[VIDEO_NAME] == ["public.movie"]


@pytest.mark.ffmpeg
def test_video_bundle_has_a_real_ffmpeg_directory_baked_in(services):
    """Claim: deleting this ships a video action that cannot find ffmpeg.

    Automator's PATH holds neither /usr/local/bin nor Homebrew's, and unlike
    `compress` every video preset shells out to ffmpeg on every file."""
    _install(services)
    wf = plistlib.loads(
        (services / f"{VIDEO_NAME}.workflow" / "Contents" / "document.wflow").read_bytes())
    script = wf["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert "@FFMPEG_DIR@" not in script, "placeholder left unsubstituted"
    m = re.search(r'^FFMPEG_DIR="([^"]*)"', script, re.M)
    assert m, "no FFMPEG_DIR assignment in the installed script"
    baked = Path(m.group(1))
    assert (baked / "ffmpeg").exists(), f"baked a directory with no ffmpeg in it: {baked}"


def test_uninstall_removes_both_bundles(services):
    _install(services)
    env = {**os.environ, "VIDTOOLS_SERVICES_DIR": str(services)}
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    for name in (NAME, VIDEO_NAME):
        assert not (services / f"{name}.workflow").exists(), f"{name} survived uninstall"


@pytest.mark.ffmpeg
def test_both_bundles_are_replaced_not_overwritten_on_reinstall(services):
    """The original regression, now applied to both. Rewriting files in place
    leaves the bundle directory's identity unchanged, which is what macOS keys
    its re-scan on."""
    _install(services)
    before = {n: (services / f"{n}.workflow").stat().st_ino for n in (NAME, VIDEO_NAME)}
    _install(services)
    for n in (NAME, VIDEO_NAME):
        assert (services / f"{n}.workflow").stat().st_ino != before[n], \
            f"{n}: bundle directory reused; macOS keeps its cached registration"


@pytest.fixture
def fake_repo(tmp_path):
    """A minimal repo copy, so a test can break the build without touching the
    real tree. install.sh derives REPO_ROOT from its own location, so a copied
    macos/ plus a stub .venv is a complete environment for it."""
    repo = tmp_path / "repo"
    (repo / "macos").mkdir(parents=True)
    for f in ("install.sh", "make_workflow.py", "compress-image.sh",
              "compress-video.sh"):
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


def test_a_failed_swap_never_leaves_a_destroyed_bundle(fake_repo, services):
    """Claim: deleting this lets a failure during the *swap* destroy both the
    installed bundle and the freshly built replacement.

    Staging exists so a failed install leaves the previous action intact, and
    that held for failures during generation. It did not hold for the swap: the
    EXIT trap stayed armed across `rm -rf "$BUNDLE"`, so a partial delete took
    out the old bundle while the trap deleted the new one -- strictly worse than
    the delete-then-generate order it replaced, which at least had no good build
    to throw away.

    An immutable file is the cheapest way to make the delete fail for real."""
    assert _install_from(fake_repo, services).returncode == 0
    bundle = services / f"{NAME}.workflow"
    subprocess.run(["chflags", "uchg", str(bundle / "Contents" / "Info.plist")],
                   check=True)
    try:
        _install_from(fake_repo, services)
        present = {p.name for p in (bundle / "Contents").iterdir()}
        assert present == {"document.wflow", "Info.plist"}, (
            f"bundle left incomplete after a failed swap: {sorted(present)}")
    finally:
        # Must run, or tmp_path teardown cannot remove the tree.
        subprocess.run(["chflags", "-R", "nouchg", str(bundle)], check=False)


def test_cleanup_restores_the_bundle_even_when_the_staging_dir_cannot_be_removed(
        tmp_path):
    """Claim: deleting this lets `set -e` abort the EXIT trap partway.

    `cleanup()` removes the staging dir, then restores the previous bundle if
    the slot ended up empty. Written as `[ -n "$STAGE" ] && rm -rf "$STAGE"`,
    the rm is the last command of an AND-list, so under `set -e` a *failing* rm
    aborts the whole function and the restore below it never runs -- producing
    the precise outcome the staging design exists to prevent: nothing
    installed, and the old bundle stranded under a dotted backup name.

    Driven as a unit rather than through a full install: reaching this state
    from the outside needs `mv "$STAGE" "$BUNDLE"` to fail *and* the staging
    dir to be unremovable at the same moment, which cannot be staged reliably.
    The function text is lifted verbatim from the real script, so this is the
    shipped code, just called directly."""
    src = INSTALL.read_text()
    start = src.index("cleanup() {")
    end = src.index("\n}\n", start) + len("\n}\n")
    cleanup_fn = src[start:end]

    stage = tmp_path / "stage"
    (stage / "Contents").mkdir(parents=True)
    stuck = stage / "Contents" / "stuck"
    stuck.write_text("x")
    subprocess.run(["chflags", "uchg", str(stuck)], check=True)   # rm -rf will fail

    previous = tmp_path / ".previous"
    previous.mkdir()
    (previous / "marker").write_text("the old bundle")
    bundle = tmp_path / "Compress Image.workflow"                 # deliberately absent

    try:
        r = subprocess.run(
            ["bash", "-c",
             f'set -euo pipefail\n'
             f'STAGE={stage!s}\nPREVIOUS={previous!s}\nBUNDLE="{bundle!s}"\n'
             f'{cleanup_fn}\ncleanup\necho REACHED_END\n'],
            capture_output=True, text=True)
        assert bundle.exists(), (
            "previous bundle not restored -- set -e aborted the trap before the "
            f"restore block. stdout={r.stdout!r} stderr={r.stderr!r}")
        assert (bundle / "marker").read_text() == "the old bundle"
        assert "REACHED_END" in r.stdout, "cleanup() did not run to completion"
    finally:
        subprocess.run(["chflags", "-R", "nouchg", str(tmp_path)], check=False)


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


def test_missing_ffmpeg_skips_the_video_action_but_still_installs_the_image_one(
        fake_repo, services):
    """Claim: deleting this either ships a video action that fails on every
    file, or refuses to install the image action over a dependency it does not
    have.

    `compress` is pure PIL, so the image action needs no ffmpeg at all.
    Blocking it because the *video* prerequisites are missing punishes the
    wrong user; installing a video action with no ffmpeg to call ships a
    right-click that always errors. Skip the one, install the other, say so."""
    # The system dirs only -- enough to find bash and the coreutils the script
    # calls, and neither holds an ffmpeg (Homebrew's and the static builds live
    # elsewhere). An empty PATH would fail for the wrong reason: no `bash`.
    r = _install_from(fake_repo, services, extra_env={"PATH": "/usr/bin:/bin"})

    assert r.returncode == 0, f"refused to install anything: {r.stderr}"
    assert (services / f"{NAME}.workflow").exists(), "image action was not installed"
    assert not (services / f"{VIDEO_NAME}.workflow").exists(), \
        "installed a video action with no ffmpeg for it to call"
    assert "ffmpeg" in (r.stderr + r.stdout).lower(), "skipped the video action silently"


@pytest.mark.ffmpeg
def test_a_failed_video_build_leaves_the_image_action_installed(fake_repo, services):
    """Claim: deleting this lets a fault in the second action take out the
    first. They are independent bundles and must fail independently."""
    assert _install_from(fake_repo, services).returncode == 0
    contents = services / f"{NAME}.workflow" / "Contents"
    before = {f.name: f.read_bytes() for f in contents.iterdir()}

    # Break only the video script, by removing the placeholder the generator
    # refuses to proceed without.
    (fake_repo / "macos" / "compress-video.sh").write_text(
        "#!/bin/zsh\necho no placeholder\n")
    r = _install_from(fake_repo, services)

    assert r.returncode != 0, "reported success despite a failed video build"
    after = {f.name: f.read_bytes() for f in contents.iterdir()}
    assert after == before, "a failed video build damaged the image action"


def test_uninstall_removes_the_bundle(services):
    _install(services)
    bundle = services / f"{NAME}.workflow"
    assert bundle.exists()
    env = {**os.environ, "VIDTOOLS_SERVICES_DIR": str(services)}
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not bundle.exists()
