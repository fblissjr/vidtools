"""The generated Automator Quick Action document.

Why this file exists: the Quick Action shipped three times with defects the
327-test suite could not see, because the bundle was generated inside a shell
heredoc. The sharpest was a workflowMetaData subset that installed cleanly,
passed `plutil -lint`, and never appeared in the Finder menu. macOS reports no
error for a structurally-valid-but-unregisterable bundle, so the only place
that can catch it is a test that pins the contract.

These are pure-plist assertions -- no Automator, no Finder, no install.
"""

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macos"))
import make_workflow  # noqa: E402

# The keys Finder actually reads. Every one was observed in a Quick Action that
# Automator itself wrote. Dropping any of them is the silent-failure mode.
REQUIRED_METADATA_KEYS = {
    "applicationBundleID", "applicationBundleIDsByPath", "applicationPath",
    "applicationPaths", "inputTypeIdentifier", "outputTypeIdentifier",
    "presentationMode", "processesInput", "serviceApplicationBundleID",
    "serviceApplicationPath", "serviceInputTypeIdentifier",
    "serviceOutputTypeIdentifier", "serviceProcessesInput", "systemImageName",
    "useAutomaticInputType", "workflowTypeIdentifier",
}


def test_metadata_carries_every_key_finder_reads():
    md = make_workflow.build_workflow("echo hi")["workflowMetaData"]
    missing = REQUIRED_METADATA_KEYS - set(md)
    assert not missing, (
        f"missing {sorted(missing)}; a metadata subset installs without error "
        "and never appears in the Finder menu"
    )


def test_presentation_mode_is_quick_action():
    """15 is a Quick Action. 512 loads without complaint and never shows up."""
    doc = make_workflow.build_workflow("echo hi")
    assert doc["workflowMetaData"]["presentationMode"] == 15
    assert doc["AMApplicationBuild"] == "541"


def test_service_and_plain_type_identifiers_agree():
    md = make_workflow.build_workflow("echo hi")["workflowMetaData"]
    assert md["inputTypeIdentifier"] == md["serviceInputTypeIdentifier"]
    assert md["outputTypeIdentifier"] == md["serviceOutputTypeIdentifier"]


def test_shell_is_zsh_not_bash():
    """/bin/bash is 3.2.57 on macOS, where `set -u` + empty-array expansion is
    fatal -- it killed the one preset that passes no flags."""
    params = make_workflow.build_workflow("echo hi")["actions"][0]["action"]["ActionParameters"]
    assert params["shell"] == "/bin/zsh"


def test_input_arrives_as_arguments_not_stdin():
    params = make_workflow.build_workflow("echo hi")["actions"][0]["action"]["ActionParameters"]
    assert params["inputMethod"] == 1


def test_script_is_embedded_verbatim():
    script = "#!/bin/zsh\necho \"quoted 'thing' $VAR\"\n"
    doc = make_workflow.build_workflow(script)
    assert doc["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"] == script


def test_document_round_trips_through_plistlib(tmp_path):
    """A plist that cannot be written is a bundle macOS silently ignores."""
    doc = make_workflow.build_workflow("#!/bin/zsh\necho 'x'\n")
    path = tmp_path / "document.wflow"
    path.write_bytes(plistlib.dumps(doc))
    assert plistlib.loads(path.read_bytes()) == doc


def test_shipped_script_has_no_unsubstituted_placeholder():
    """The repo copy must keep @VIDTOOLS_BIN@ (an absolute path here is a leak);
    the generated copy must not (it would be a broken action)."""
    src = Path(__file__).resolve().parents[1] / "macos" / "compress-image.sh"
    text = src.read_text()
    assert "@VIDTOOLS_BIN@" in text
    generated = make_workflow.build_workflow(text.replace("@VIDTOOLS_BIN@", "/tmp/vidtools"))
    assert "@VIDTOOLS_BIN@" not in \
        generated["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]


@pytest.mark.skipif(sys.platform != "darwin", reason="plutil is macOS-only")
def test_generated_plist_passes_plutil(tmp_path):
    doc = make_workflow.build_workflow("#!/bin/zsh\necho 'x'\n")
    path = tmp_path / "document.wflow"
    path.write_bytes(plistlib.dumps(doc))
    r = subprocess.run(["plutil", "-lint", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# The Info.plist is what actually registers the service. It carries the same
# silent-failure mode as document.wflow -- drop a key and the bundle installs
# cleanly, lints fine, and never appears in the menu -- so it gets the same
# pinned-contract treatment rather than living in an untested heredoc.

REQUIRED_SERVICE_KEYS = {
    "NSMenuItem", "NSMessage", "NSRequiredContext", "NSSendFileTypes",
}


def test_info_plist_carries_every_registration_key():
    svc = make_workflow.build_info_plist("Compress Image")["NSServices"][0]
    missing = REQUIRED_SERVICE_KEYS - set(svc)
    assert not missing, f"missing {sorted(missing)}; the action will not register"


def test_info_plist_targets_finder_and_images():
    svc = make_workflow.build_info_plist("Compress Image")["NSServices"][0]
    assert svc["NSMessage"] == "runWorkflowAsService"
    assert svc["NSRequiredContext"]["NSApplicationIdentifier"] == "com.apple.finder"
    assert svc["NSSendFileTypes"] == ["public.image"]


# ------------------------------------------------------------------ two kinds
#
# The video action is a second bundle rather than a second preset list inside
# the image one, so the module has to build both. Both identifiers below were
# read off Apple's own shipping Quick Action ("Encode Selected Video Files"),
# not guessed -- the same method that produced the image metadata set, and for
# the same reason: a wrong-but-plausible identifier registers without error and
# never appears in the menu.

def test_video_kind_declares_the_movie_input_type():
    md = make_workflow.build_workflow("echo hi", kind="video")["workflowMetaData"]
    assert md["serviceInputTypeIdentifier"] == "com.apple.Automator.fileSystemObject.movie"
    assert md["inputTypeIdentifier"] == md["serviceInputTypeIdentifier"]


def test_video_info_plist_registers_for_movies_only():
    """Claim: deleting this lets the video action register for public.image.

    It would then appear on right-clicking a *photo*, offering video presets
    that cannot run, while never appearing on the videos it is for."""
    svc = make_workflow.build_info_plist("Compress Video", kind="video")["NSServices"][0]
    assert svc["NSSendFileTypes"] == ["public.movie"]


def test_image_kind_is_unchanged_and_is_the_default():
    """The image action predates the video one; adding a second kind must not
    move it. An unqualified call still builds the image bundle."""
    default = make_workflow.build_workflow("echo hi")["workflowMetaData"]
    explicit = make_workflow.build_workflow("echo hi", kind="image")["workflowMetaData"]
    assert default == explicit
    assert default["serviceInputTypeIdentifier"] == \
        "com.apple.Automator.fileSystemObject.image"
    assert make_workflow.build_info_plist("x")["NSServices"][0]["NSSendFileTypes"] == \
        ["public.image"]


def test_the_two_kinds_do_not_share_action_uuids():
    """Claim: deleting this leaves both bundles carrying identical action UUIDs.

    They were literals when only one bundle existed. Two documents keyed the
    same is the kind of thing that works until the day Automator dedupes on
    it, and costs nothing to avoid."""
    img = make_workflow.build_workflow("echo hi", kind="image")["actions"][0]["action"]
    vid = make_workflow.build_workflow("echo hi", kind="video")["actions"][0]["action"]
    assert img["UUID"] != vid["UUID"]
    assert img["InputUUID"] != vid["InputUUID"]
    assert img["OutputUUID"] != vid["OutputUUID"]


def test_an_unknown_kind_is_refused():
    """Fail closed rather than silently building an image bundle under a video
    name -- which is a bundle that registers for the wrong file type."""
    with pytest.raises((KeyError, ValueError, SystemExit)):
        make_workflow.build_workflow("echo hi", kind="audio")


def test_info_plist_menu_name_follows_the_bundle_name():
    """The menu entry and the bundle directory must agree, or the action
    installs under one name and appears under another."""
    svc = make_workflow.build_info_plist("Some Name")["NSServices"][0]
    assert svc["NSMenuItem"]["default"] == "Some Name"


def test_info_plist_round_trips(tmp_path):
    doc = make_workflow.build_info_plist("Compress Image")
    p = tmp_path / "Info.plist"
    p.write_bytes(plistlib.dumps(doc))
    assert plistlib.loads(p.read_bytes()) == doc


# --------------------------------------------------------------- main() guards

MAKE_WORKFLOW = Path(__file__).resolve().parents[1] / "macos" / "make_workflow.py"


def _run_main(tmp_path, script_text, bin_path="/somewhere/vidtools", **extra_env):
    src = tmp_path / "script.sh"
    src.write_text(script_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(MAKE_WORKFLOW),
         str(tmp_path / "document.wflow"), str(tmp_path / "Info.plist")],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "SCRIPT_SRC": str(src),
             "BIN": bin_path, "NAME": "Compress Image",
             "FFMPEG_DIR": "/somewhere/bin", **extra_env},
    )


def _command_string(tmp_path):
    wf = plistlib.loads((tmp_path / "document.wflow").read_bytes())
    return wf["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]


def test_missing_placeholder_is_refused(tmp_path):
    """Claim: deleting this lets a renamed placeholder install silently.

    `.replace()` on a string that lacks the token is a no-op, so the bundle
    generates cleanly with no binary path in it and the action fails later with
    "vidtools binary not found". The existing installer test cannot catch this:
    it asserts `@VIDTOOLS_BIN@` is *absent* from the output, which a rename
    satisfies trivially."""
    r = _run_main(tmp_path, "#!/bin/zsh\necho no placeholder here\n")
    assert r.returncode != 0, "generated a bundle with no binary path in it"
    assert "@VIDTOOLS_BIN@" in (r.stderr + r.stdout), r.stderr


def test_placeholder_present_still_generates(tmp_path):
    r = _run_main(tmp_path, "#!/bin/zsh\nVIDTOOLS=\"@VIDTOOLS_BIN@\"\n")
    assert r.returncode == 0, r.stderr
    wf = plistlib.loads((tmp_path / "document.wflow").read_bytes())
    assert "/somewhere/vidtools" in wf["actions"][0]["action"]["ActionParameters"][
        "COMMAND_STRING"]


def test_ffmpeg_dir_is_substituted(tmp_path):
    """Claim: deleting this ships a video action that cannot find ffmpeg.

    Automator runs with a minimal PATH holding neither /usr/local/bin nor
    /opt/homebrew/bin. The image action never noticed because `compress` is pure
    PIL; every video preset shells out to ffmpeg on every file."""
    r = _run_main(tmp_path, '#!/bin/zsh\nBIN="@VIDTOOLS_BIN@"\nPATH="@FFMPEG_DIR@:$PATH"\n')
    assert r.returncode == 0, r.stderr
    script = _command_string(tmp_path)
    assert "@FFMPEG_DIR@" not in script
    assert "/somewhere/bin" in script


def test_a_renamed_placeholder_is_refused(tmp_path):
    """Claim: deleting this lets a typo'd placeholder ship as literal text.

    This is the gap the installer-side check provably cannot cover: it asserts
    @VIDTOOLS_BIN@ is *absent* from the output, which a renamed token satisfies
    trivially. Only the generator can see that a token nothing will substitute
    was left in the script."""
    r = _run_main(tmp_path, '#!/bin/zsh\nBIN="@VIDTOOLS_BIN@"\nD="@FFMPEG_PATH@"\n')
    assert r.returncode != 0, "shipped a script containing an unsubstituted token"
    assert "@FFMPEG_PATH@" in (r.stderr + r.stdout), r.stderr


def test_non_ascii_script_is_read_as_utf8(tmp_path):
    """The real script's AppleScript block uses `¬` continuations. Reading it
    under a locale-dependent encoding is a decode away from failing."""
    r = _run_main(tmp_path, '#!/bin/zsh\n# @VIDTOOLS_BIN@ ¬ ¬ ¬\n')
    assert r.returncode == 0, r.stderr
    wf = plistlib.loads((tmp_path / "document.wflow").read_bytes())
    assert "¬" in wf["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
