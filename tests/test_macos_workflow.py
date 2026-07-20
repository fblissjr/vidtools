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
    assert make_workflow.build_workflow("echo hi")["workflowMetaData"]["presentationMode"] == 15


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
