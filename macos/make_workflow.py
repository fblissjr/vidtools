"""Generate the Automator Quick Action workflow document.

Extracted from install.sh so the plist can be tested. Nothing here needs the
vidtools package -- it is stdlib-only on purpose, so install.sh can run it with
the system python3 before (or without) a working venv.

The metadata keys in `_WORKFLOW_METADATA` are not a plausible-looking subset.
They were arrived at by diffing against a Quick Action that Automator itself
wrote. A subset installs cleanly, passes `plutil -lint`, registers nothing, and
silently never appears in the Finder menu -- which is exactly how the bug that
motivated this module presented. `tests/test_macos_workflow.py` pins the key
set so trimming it back fails loudly instead of silently.
"""

import os
import plistlib
import sys

FINDER = "/System/Library/CoreServices/Finder.app"
IMAGE_INPUT = "com.apple.Automator.fileSystemObject.image"

# Finder reads the non-`service`-prefixed twins; the service* keys alone are not
# enough. presentationMode is 15 (Quick Action), not 512.
_WORKFLOW_METADATA = {
    "applicationBundleID": "com.apple.finder",
    "applicationBundleIDsByPath": {FINDER: "com.apple.finder"},
    "applicationPath": FINDER,
    "applicationPaths": [FINDER],
    "inputTypeIdentifier": IMAGE_INPUT,
    "outputTypeIdentifier": "com.apple.Automator.nothing",
    "presentationMode": 15,
    "processesInput": False,
    "serviceApplicationBundleID": "com.apple.finder",
    "serviceApplicationPath": FINDER,
    "serviceInputTypeIdentifier": IMAGE_INPUT,
    "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
    "serviceProcessesInput": False,
    "systemImageName": "NSActionTemplate",
    "useAutomaticInputType": False,
    "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
}


def build_action(script: str) -> dict:
    """The single Run Shell Script action carrying the compress script."""
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
        "InputUUID": "vidtools-compress-in",
        "OutputUUID": "vidtools-compress-out",
        "UUID": "vidtools-compress-action",
        "UnlocalizedApplications": ["Automator"],
        "arguments": {},
        "isViewVisible": 1,
    }}


def build_workflow(script: str) -> dict:
    """Return the complete .wflow document."""
    return {
        "AMApplicationBuild": "526",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [build_action(script)],
        "connectors": {},
        "workflowMetaData": dict(_WORKFLOW_METADATA),
    }


def main() -> None:
    script = open(os.environ["SCRIPT_SRC"]).read().replace(
        "@VIDTOOLS_BIN@", os.environ["BIN"])
    with open(sys.argv[1], "wb") as fh:
        plistlib.dump(build_workflow(script), fh)


if __name__ == "__main__":
    main()
