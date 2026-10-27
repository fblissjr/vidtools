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


def _test_env(services: Path, extra=None) -> dict:
    """Environment for a test install.

    `VIDTOOLS_PBS_DOMAIN` is not optional here. Without it `install.sh` falls
    back to the real `pbs` domain, and every test install performs a
    read-modify-write of the user's live preferences — adding enable records for
    bundles that exist only under `tmp_path`. Derived from `services` so each
    test gets its own throwaway plist.
    """
    return {**os.environ,
            "VIDTOOLS_SERVICES_DIR": str(services),
            "VIDTOOLS_PBS_DOMAIN": str(services.parent / "pbs-test.plist"),
            **(extra or {})}


def _install(services: Path):
    return subprocess.run(["bash", str(INSTALL)], capture_output=True, text=True,
                          env=_test_env(services))


def _shell_functions(*names: str) -> str:
    """Lift named function definitions verbatim out of install.sh.

    Lets a test drive the real shipped code as a unit when the state it needs
    cannot be reached by running the whole installer.
    """
    src = INSTALL.read_text()
    out = []
    for name in names:
        start = src.index(f"{name}() {{")
        end = src.index("\n}\n", start) + len("\n}\n")
        out.append(src[start:end])
    return "\n".join(out)


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
    env = _test_env(services)
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
              "compress-video.sh", "enable_service.py"):
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
    return subprocess.run(["bash", str(repo / "macos" / "install.sh")],
                          capture_output=True, text=True,
                          env=_test_env(services, extra_env))


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
        # Must run, or tmp_path teardown cannot remove the tree. The whole
        # Services dir, not just the bundle: the swap renames the old bundle --
        # locked file and all -- to a `.previous` backup it cannot delete, so
        # unlocking only the bundle path stranded that backup in pytest's
        # temp root, undeletable, on every run.
        subprocess.run(["chflags", "-R", "nouchg", str(services)], check=False)


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
    fns = _shell_functions("remove_generated_bundle", "cleanup")

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
             f'SERVICES={tmp_path!s}\n'
             f'STAGE={stage!s}\nPREVIOUS={previous!s}\nBUNDLE="{bundle!s}"\n'
             f'{fns}\ncleanup\necho REACHED_END\n'],
            capture_output=True, text=True)
        assert bundle.exists(), (
            "previous bundle not restored -- set -e aborted the trap before the "
            f"restore block. stdout={r.stdout!r} stderr={r.stderr!r}")
        assert (bundle / "marker").read_text() == "the old bundle"
        assert "REACHED_END" in r.stdout, "cleanup() did not run to completion"
    finally:
        subprocess.run(["chflags", "-R", "nouchg", str(tmp_path)], check=False)


# ------------------------------------------------- the enable/disable record
#
# A bundle can be complete, lint-clean and present in the services database and
# still never appear -- not in the Finder menu, and not in System Settings, so
# there is nowhere to switch it on. The only difference between the action that
# worked and the one that did not was this record. `VIDTOOLS_PBS_DOMAIN` points
# these at a throwaway plist so they never touch the real `pbs` domain.

@pytest.fixture
def pbs_domain(tmp_path):
    return tmp_path / "pbs-test.plist"


def _status(domain: Path) -> dict:
    if not domain.exists():
        return {}
    return plistlib.loads(domain.read_bytes()).get("NSServicesStatus", {})


def test_install_writes_an_enable_record_for_each_action(services, pbs_domain):
    """Claim: deleting this ships an action that registers correctly and is
    invisible anyway, with no way for the user to switch it on."""
    env = {**os.environ, "VIDTOOLS_SERVICES_DIR": str(services),
           "VIDTOOLS_PBS_DOMAIN": str(pbs_domain)}
    r = subprocess.run(["bash", str(INSTALL)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr

    status = _status(pbs_domain)
    for name in (NAME, VIDEO_NAME):
        key = f"(null) - {name} - runWorkflowAsService"
        if name == VIDEO_NAME and not (services / f"{name}.workflow").exists():
            continue                      # skipped for want of ffmpeg; fine
        assert key in status, f"no enable record for {name}: {sorted(status)}"
        assert status[key]["presentation_modes"]["ContextMenu"] is True


def test_install_never_overturns_an_existing_preference(services, pbs_domain):
    """Claim: deleting this lets a reinstall switch an action the user turned
    *off* back on. An existing entry is their decision, not ours to revise."""
    key = f"(null) - {NAME} - runWorkflowAsService"
    pbs_domain.write_bytes(plistlib.dumps({"NSServicesStatus": {
        key: {"presentation_modes": {"ContextMenu": False, "FinderPreview": False,
                                     "ServicesMenu": False, "TouchBar": False}}}}))

    env = {**os.environ, "VIDTOOLS_SERVICES_DIR": str(services),
           "VIDTOOLS_PBS_DOMAIN": str(pbs_domain)}
    assert subprocess.run(["bash", str(INSTALL)], capture_output=True,
                          text=True, env=env).returncode == 0

    assert _status(pbs_domain)[key]["presentation_modes"]["ContextMenu"] is False, \
        "reinstall re-enabled an action the user had switched off"


def test_a_failure_writing_the_record_does_not_fail_the_install(fake_repo, services):
    """Claim: deleting the `|| echo` guard aborts a *successful* install under
    `set -e` because a cosmetic step failed. The bundle is on disk by that
    point; losing a pre-written toggle is not worth throwing it away.

    The failure has to be staged by breaking the script itself: `defaults
    import` exits 0 even when it cannot write, so no domain path reaches this
    branch. An earlier version of this test passed for exactly that reason
    while testing nothing."""
    (fake_repo / "macos" / "enable_service.py").write_text(
        "import sys\nsys.exit('STUB-EXPLODED')\n")

    r = _install_from(fake_repo, services)
    assert r.returncode == 0, f"a cosmetic failure aborted the install: {r.stderr}"
    assert (services / f"{NAME}.workflow").exists()
    # Match the installer's own wording, not the stub's. An earlier version
    # asserted on a substring both messages contained, so it stayed green when
    # the note was deleted.
    assert "note: could not write" in r.stderr, \
        f"installer swallowed the failure instead of noting it: {r.stderr!r}"


ENABLE = REPO / "macos" / "enable_service.py"


def _enable(name, domain, python=sys.executable):
    return subprocess.run([python, str(ENABLE), name, str(domain)],
                          capture_output=True, text=True)


def test_enable_service_does_not_claim_success_when_the_write_is_lost(tmp_path):
    """Claim: deleting the read-back lets the installer print "enabled:" over a
    write that never landed.

    `defaults import` exits 0 whether or not it wrote anything — verified
    against an unwritable directory. Trusting its exit code puts the user back
    in the exact invisible-action state this feature exists to fix, with a
    success message saying it was fixed."""
    ro = tmp_path / "ro"
    ro.mkdir()
    domain = ro / "x.plist"
    domain.write_bytes(plistlib.dumps({}))
    ro.chmod(0o500)
    try:
        r = _enable("Test Action", domain)
        assert r.returncode != 0, (
            f"reported success for a write that did not land: {r.stdout!r}")
        assert "enabled:" not in r.stdout
    finally:
        ro.chmod(0o700)


def test_enable_service_keeps_every_other_service_entry(tmp_path):
    """Claim: deleting this lets the enable record wipe every other Quick
    Action's toggle.

    `defaults import` merges at the top level but *replaces* a nested key
    outright — verified. So writing an NSServicesStatus built from anything
    less than the full current contents silently drops the rest."""
    domain = tmp_path / "pbs.plist"
    other = "(null) - Someone Elses Action - runWorkflowAsService"
    subprocess.run(["defaults", "import", str(domain), "-"],
                   input=plistlib.dumps({
                       "SomeUnrelatedTopLevelKey": "keep me",
                       "NSServicesStatus": {other: {"presentation_modes": {"ContextMenu": False}}},
                   }), check=True)

    assert _enable("Compress Video", domain).returncode == 0
    after = plistlib.loads(subprocess.run(
        ["defaults", "export", str(domain), "-"], capture_output=True).stdout)

    status = after.get("NSServicesStatus", {})
    assert other in status, "wiped another service's enable record"
    assert status[other]["presentation_modes"]["ContextMenu"] is False, "altered it"
    assert "(null) - Compress Video - runWorkflowAsService" in status
    assert after.get("SomeUnrelatedTopLevelKey") == "keep me"


def test_enable_service_writes_nothing_when_the_read_comes_back_empty(tmp_path):
    """Claim: deleting this makes a failed *read* destructive.

    `defaults export` emits a full `<dict/>` plist even for a domain that has
    never existed, so empty output means the read failed, not "no entries yet".
    The old code defaulted it to `{}` — and because `defaults import` *replaces*
    nested keys, writing a status dict rebuilt from nothing erases every other
    Quick Action's toggle.

    Asserted on whether an import happened at all, not on the exit code: a bare
    `plistlib.loads(b"")` also exits non-zero, so a returncode assertion cannot
    tell the guard from a crash. The stub records any import it is handed."""
    fake = tmp_path / "bin"
    fake.mkdir()
    marker = tmp_path / "import-happened"
    (fake / "defaults").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "export" ]; then exit 0; fi\n'        # empty read
        f'if [ "$1" = "import" ]; then cat > "{marker}"; fi\n'
        "exit 0\n")
    (fake / "defaults").chmod(0o755)

    r = subprocess.run([sys.executable, str(ENABLE), "Compress Video", "whatever"],
                       capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"})

    assert not marker.exists(), (
        "wrote preferences off a failed read -- this is what erases every other "
        "service's enable record")
    assert r.returncode != 0
    assert "enabled:" not in r.stdout


def test_test_installs_never_touch_the_real_preference_domain():
    """Claim: deleting this lets the suite read-modify-write the user's live
    `pbs` domain on every install test.

    `install.sh` falls back to `${VIDTOOLS_PBS_DOMAIN:-pbs}`, so a helper that
    forgets the variable silently edits real preferences — adding enable records
    for bundles that only ever existed under `tmp_path`. Both helpers went out
    that way before this."""
    env = _test_env(Path("/tmp/whatever/Services"))
    domain = env.get("VIDTOOLS_PBS_DOMAIN")
    assert domain, "test installs would fall back to the real 'pbs' domain"
    assert domain != "pbs" and domain.endswith(".plist"), domain

    # And no helper may build its own environment and skip it. Scanned by
    # statement rather than by line: these env dicts span several lines, so a
    # line-based check reports every multi-line one as a miss.
    src = Path(__file__).read_text()
    stray = []
    start = 0
    while (i := src.find("VIDTOOLS_SERVICES_DIR", start)) != -1:
        if "VIDTOOLS_PBS_DOMAIN" not in src[i:i + 200]:
            stray.append(src[max(0, i - 60):i + 60].replace("\n", " "))
        start = i + 1
    assert stray == [], f"environment built without the test pbs domain: {stray}"


def test_the_installer_never_deletes_recursively():
    """Claim: deleting this lets `rm -rf` back into a script whose paths are
    built from variables and which runs against the real Services folder.

    Recursion buys nothing here -- every directory this script removes holds
    exactly the two files it generated -- so `-r` is pure blast radius. The
    replacement is `rm -f` on named files plus `rmdir`, which refuses a
    directory that still holds anything and therefore cannot cascade."""
    offenders = [ln.strip() for ln in INSTALL.read_text().splitlines()
                 if "rm -rf" in ln and not ln.lstrip().startswith("#")]
    assert offenders == [], f"recursive delete reintroduced: {offenders}"


def test_removal_leaves_unexpected_files_alone_and_says_so(services):
    """Claim: deleting this lets uninstall destroy files it did not create.

    `rm -rf` would take a bundle a user had dropped their own files into
    without a word. `rmdir` refuses, so the directory survives and the script
    reports it rather than reporting a clean removal that silently wasn't."""
    _install(services)
    bundle = services / f"{NAME}.workflow"
    mine = bundle / "Contents" / "notes-of-my-own.txt"
    mine.write_text("do not delete me")

    env = _test_env(services)
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)

    assert mine.exists(), "uninstall destroyed a file it did not create"
    assert mine.read_text() == "do not delete me"
    assert r.returncode != 0, "reported a clean uninstall that did not happen"
    assert "Left in place" in r.stderr, r.stderr


def test_removal_tolerates_the_ds_store_finder_leaves_behind(services):
    """Opening the Services folder in Finder is enough to create one, and it
    would otherwise block every uninstall with a scary warning."""
    _install(services)
    bundle = services / f"{NAME}.workflow"
    (bundle / "Contents" / ".DS_Store").write_bytes(b"\x00\x01")

    env = _test_env(services)
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not bundle.exists()


def test_removal_does_not_claim_success_over_a_non_directory(services):
    """Claim: deleting this lets uninstall report "removed" for something still
    on disk that it never touched.

    `[ -d "$dir" ] || return 0` read anything that is not a directory as
    "nothing to do" and returned *success*, so a plain file sitting where a
    bundle belongs printed "removed: Compress Image" and exited 0 while the file
    was untouched. The function's contract is that non-zero means something was
    left behind; a false clean bill is worse than the warning."""
    stray = services / f"{NAME}.workflow"
    stray.write_text("not a bundle at all")

    env = _test_env(services)
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)

    assert stray.exists(), "removed something that was not a bundle we created"
    assert r.returncode != 0, "reported a clean uninstall of a file still on disk"
    assert f"removed: {NAME}" not in r.stdout, r.stdout


def test_removal_refuses_a_path_outside_the_services_directory(tmp_path):
    """Claim: deleting the containment check lets a future edit that makes one
    of these paths computable to something else delete it anyway."""
    outside = tmp_path / "not-services" / "Something.workflow"
    (outside / "Contents").mkdir(parents=True)
    (outside / "Contents" / "Info.plist").write_text("x")

    r = subprocess.run(
        ["bash", "-c",
         f'set -euo pipefail\nSERVICES={tmp_path / "services"!s}\n'
         f'{_shell_functions("remove_generated_bundle")}\n'
         f'remove_generated_bundle "{outside!s}" || echo REFUSED\n'],
        capture_output=True, text=True)
    assert outside.exists(), "removed a directory outside the Services folder"
    assert "REFUSED" in r.stdout
    assert "refusing to remove" in r.stderr


def test_uninstall_reports_when_nothing_was_installed(services):
    env = _test_env(services)
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "not installed" in r.stdout


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
    env = _test_env(services)
    r = subprocess.run(["bash", str(INSTALL), "--uninstall"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not bundle.exists()


def test_prefers_keg_only_ffmpeg_full_when_present(fake_repo, services, tmp_path):
    """Homebrew's `ffmpeg-full` is keg-only, living at `<prefix>/opt/ffmpeg-full/bin`
    while `<prefix>/bin/ffmpeg` may point to the slim formula (which lacks
    libx265 and libass). The installer should bake the `ffmpeg-full` bin dir
    into the video Quick Action when present."""
    prefix = tmp_path / "hb"
    slim_bin = prefix / "bin"
    full_bin = prefix / "opt" / "ffmpeg-full" / "bin"
    slim_bin.mkdir(parents=True)
    full_bin.mkdir(parents=True)
    for d in (slim_bin, full_bin):
        ff = d / "ffmpeg"
        ff.write_text("#!/bin/sh\nexit 0\n")
        ff.chmod(0o755)

    r = _install_from(
        fake_repo, services,
        extra_env={"PATH": f"{slim_bin}:/usr/bin:/bin"},
    )
    assert r.returncode == 0, r.stderr
    wf = plistlib.loads(
        (services / f"{VIDEO_NAME}.workflow" / "Contents" / "document.wflow").read_bytes()
    )
    script = wf["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert f'FFMPEG_DIR="{full_bin}"' in script
