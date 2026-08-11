"""Generate the Automator Quick Action workflow document.

Extracted from install.sh so the plist can be tested. Stdlib-only on purpose:
nothing here imports the vidtools package, so generating a bundle cannot fail
for a reason that belongs to the thing being packaged. install.sh runs it with
the venv's own interpreter, after checking the venv resolves -- an earlier
version used PATH's `python3` on the theory that this file had to work without
a venv, but install.sh has required one since well before it gets here.

The metadata keys in `build_metadata` are not a plausible-looking subset.
They were arrived at by diffing against a Quick Action that Automator itself
wrote. A subset installs cleanly, passes `plutil -lint`, registers nothing, and
silently never appears in the Finder menu -- which is exactly how the bug that
motivated this module presented. `tests/test_macos_workflow.py` pins the key
set so trimming it back fails loudly instead of silently.
"""

import os
import plistlib
import re
import sys

FINDER = "/System/Library/CoreServices/Finder.app"

# One bundle per media kind, rather than one bundle accepting both. Finder then
# offers each entry only on the files it can act on, and neither script has to
# cope with a mixed selection -- which has no correct preset list.
#
# Both identifiers per kind were read off a Quick Action macOS itself ships
# ("Encode Selected Video Files" for the movie pair), not inferred. A
# wrong-but-plausible identifier is the silent failure this whole module is
# shaped around: it installs cleanly, lints, and never appears in the menu.
KINDS = {
    "image": {
        "input_type": "com.apple.Automator.fileSystemObject.image",
        "file_types": ["public.image"],
    },
    "video": {
        "input_type": "com.apple.Automator.fileSystemObject.movie",
        "file_types": ["public.movie"],
    },
}


def _kind(kind: str) -> dict:
    try:
        return KINDS[kind]
    except KeyError:
        raise SystemExit(
            f"error: unknown action kind {kind!r}. Known: {sorted(KINDS)}.") from None


def build_metadata(kind: str = "image") -> dict:
    """Finder reads the non-`service`-prefixed twins; the service* keys alone
    are not enough. presentationMode is 15 (Quick Action), not 512."""
    input_type = _kind(kind)["input_type"]
    return {
        "applicationBundleID": "com.apple.finder",
        "applicationBundleIDsByPath": {FINDER: "com.apple.finder"},
        "applicationPath": FINDER,
        "applicationPaths": [FINDER],
        "inputTypeIdentifier": input_type,
        "outputTypeIdentifier": "com.apple.Automator.nothing",
        "presentationMode": 15,
        "processesInput": False,
        "serviceApplicationBundleID": "com.apple.finder",
        "serviceApplicationPath": FINDER,
        "serviceInputTypeIdentifier": input_type,
        "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
        "serviceProcessesInput": False,
        "systemImageName": "NSActionTemplate",
        "useAutomaticInputType": False,
        "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
    }


def build_action(script: str, kind: str = "image") -> dict:
    """The single Run Shell Script action carrying the compress script."""
    _kind(kind)
    return {"action": {
        "AMAccepts": {"Container": "List", "Optional": True,
                      "Types": ["com.apple.cocoa.string"]},
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {k: {} for k in
            ("COMMAND_STRING", "CheckedForUserDefaultShell", "inputMethod", "shell", "source")},
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {
            "COMMAND_STRING": script,
            "CheckedForUserDefaultShell": True,
            "inputMethod": 1,          # pass the selection as arguments, not stdin
            # zsh, not bash: /bin/bash on macOS is 3.2.57, where `set -u` plus an
            # empty-array expansion is a fatal "unbound variable".
            "shell": "/bin/zsh",
            "source": "",
        },
        "BundleIdentifier": "com.apple.RunShellScript",
        "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "Class Name": "RunShellScriptAction",
        # Per-kind, not shared literals. Two bundles keyed identically works
        # until something dedupes on it, and distinguishing them costs nothing.
        "InputUUID": f"vidtools-{kind}-in",
        "OutputUUID": f"vidtools-{kind}-out",
        "UUID": f"vidtools-{kind}-action",
        "UnlocalizedApplications": ["Automator"],
        "arguments": {},
        "isViewVisible": 1,
    }}


def build_workflow(script: str, kind: str = "image") -> dict:
    """Return the complete .wflow document."""
    return {
        "AMApplicationBuild": "526",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [build_action(script, kind)],
        "connectors": {},
        "workflowMetaData": build_metadata(kind),
    }


def build_info_plist(name: str, kind: str = "image") -> dict:
    """The bundle's Info.plist -- what actually registers the service.

    Generated here rather than in an install.sh heredoc for the same reason
    document.wflow is: these keys carry the identical silent-failure mode. A
    bundle missing NSMessage, NSRequiredContext or NSSendFileTypes installs
    cleanly, passes `plutil -lint`, and never appears in the Finder menu.
    Leaving this one in a heredoc left that failure fully reachable, one file
    away from where the fix was applied.
    """
    return {"NSServices": [{
        "NSBackgroundColorName": "background",
        "NSIconName": "NSActionTemplate",
        "NSMenuItem": {"default": name},
        "NSMessage": "runWorkflowAsService",
        "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
        "NSSendFileTypes": list(_kind(kind)["file_types"]),
    }]}


PLACEHOLDER = "@VIDTOOLS_BIN@"
_TOKEN_RE = re.compile(r"@[A-Z0-9_]+@")


def substitute(raw: str, values: dict) -> str:
    """Replace every @TOKEN@ in the script, failing closed on either mistake.

    Two distinct ways this goes wrong, and neither is visible downstream:

    * The token is *absent*. `str.replace` is then a silent no-op and the bundle
      ships carrying no binary path, failing much later and much less legibly as
      "vidtools binary not found" at right-click time.
    * The token is *misspelled* -- @FFMPEG_PATH@ for @FFMPEG_DIR@. It then ships
      as literal text in the script. This is the case the installer provably
      cannot catch: it checks that @VIDTOOLS_BIN@ is absent from the output,
      which a renamed token satisfies trivially. Only here can a token that
      nothing will substitute be seen.
    """
    if PLACEHOLDER not in raw:
        raise SystemExit(
            f"error: {PLACEHOLDER} not found in the script. The generated "
            f"action would carry no path to the vidtools binary.")
    unknown = sorted(set(_TOKEN_RE.findall(raw)) - set(values))
    if unknown:
        raise SystemExit(
            f"error: no value for placeholder(s) {unknown}. They would ship as "
            f"literal text. Known placeholders: {sorted(values)}.")
    for token, value in values.items():
        raw = raw.replace(token, value)
    return raw


def main() -> None:
    src = os.environ["SCRIPT_SRC"]
    kind = os.environ.get("KIND", "image")
    # Explicit utf-8: the script's AppleScript block uses `¬` continuations, and
    # the default here is locale-dependent. macOS happens to report utf-8 even
    # under LC_ALL=C, so this is insurance rather than a fix for an observed
    # failure -- but the cost of being wrong is a bundle that will not build.
    with open(src, encoding="utf-8") as fh:
        raw = fh.read()

    script = substitute(raw, {
        PLACEHOLDER: os.environ["BIN"],
        # Only the video script uses this; replacing a token the image script
        # does not contain is a harmless no-op.
        "@FFMPEG_DIR@": os.environ.get("FFMPEG_DIR", ""),
    })
    with open(sys.argv[1], "wb") as fh:
        plistlib.dump(build_workflow(script, kind), fh)
    with open(sys.argv[2], "wb") as fh:
        plistlib.dump(build_info_plist(os.environ["NAME"], kind), fh)


if __name__ == "__main__":
    main()
