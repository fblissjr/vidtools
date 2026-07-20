#!/bin/bash
# Build and install the "Compress Image" Finder Quick Action.
#
# The workflow bundle is generated rather than checked in, for two reasons: the
# script inside it needs this machine's absolute path to the vidtools binary
# (which must not appear in the repo), and a .wflow is an opaque plist that
# reviews badly. Generating keeps the reviewable artifact -- the shell script --
# in plain sight.
#
# Automator rather than a SwiftUI app: a signed app is a notarisation problem
# this project does not otherwise have, and the Run Shell Script action covers
# the case. The cost is that AppleScript cannot draw a multi-control panel, so
# the dialog is one preset list rather than the Format / Size / Preserve
# Metadata trio macOS's own Convert Image shows. If that trade stops being worth
# it, the script here is what a native front end would call.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
NAME="Compress Image"
SERVICES="$HOME/Library/Services"   # path-privacy: ignore -- the OS decides where Quick Actions live
BUNDLE="$SERVICES/$NAME.workflow"

# Prefer the project venv binary over PATH: Automator runs with a minimal PATH,
# and `uv run` adds startup cost plus a dependency on uv being discoverable.
BIN="$REPO_ROOT/.venv/bin/vidtools"
if [ ! -x "$BIN" ]; then
    echo "error: vidtools binary not found in the project venv. Run 'uv sync' first." >&2
    exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$BUNDLE"
    echo "removed: $NAME"
    exit 0
fi

mkdir -p "$BUNDLE/Contents"
SCRIPT_SRC="$REPO_ROOT/macos/compress-image.sh"

BIN="$BIN" SCRIPT_SRC="$SCRIPT_SRC" python3 - "$BUNDLE/Contents/document.wflow" <<'PY'
import os, plistlib, sys

script = open(os.environ["SCRIPT_SRC"]).read().replace("@VIDTOOLS_BIN@", os.environ["BIN"])

action = {"action": {
    "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
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

FINDER = "/System/Library/CoreServices/Finder.app"

plistlib.dump({
    "AMApplicationBuild": "526",
    "AMApplicationVersion": "2.10",
    "AMDocumentVersion": "2",
    "actions": [action],
    "connectors": {},
    # Every key here is present in a Quick Action that Automator itself wrote,
    # and was arrived at by diffing against one. A hand-written subset that
    # looks sufficient is not: an earlier version carried only the six
    # service* / presentationMode / workflowTypeIdentifier keys, which loads
    # without complaint and never appears in the Finder menu. The
    # non-service-prefixed twins (applicationBundleID, inputTypeIdentifier,
    # processesInput) are what Finder actually reads; the service* ones alone
    # are not enough. presentationMode is 15, not 512.
    "workflowMetaData": {
        "applicationBundleID": "com.apple.finder",
        "applicationBundleIDsByPath": {FINDER: "com.apple.finder"},
        "applicationPath": FINDER,
        "applicationPaths": [FINDER],
        "inputTypeIdentifier": "com.apple.Automator.fileSystemObject.image",
        "outputTypeIdentifier": "com.apple.Automator.nothing",
        "presentationMode": 15,
        "processesInput": False,
        "serviceApplicationBundleID": "com.apple.finder",
        "serviceApplicationPath": FINDER,
        "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject.image",
        "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
        "serviceProcessesInput": False,
        "systemImageName": "NSActionTemplate",
        "useAutomaticInputType": False,
        "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
    },
}, open(sys.argv[1], "wb"))
PY

cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSServices</key>
  <array>
    <dict>
      <key>NSBackgroundColorName</key><string>background</string>
      <key>NSIconName</key><string>NSActionTemplate</string>
      <key>NSMenuItem</key>
      <dict><key>default</key><string>$NAME</string></dict>
      <key>NSMessage</key><string>runWorkflowAsService</string>
      <key>NSRequiredContext</key>
      <dict><key>NSApplicationIdentifier</key><string>com.apple.finder</string></dict>
      <key>NSSendFileTypes</key>
      <array><string>public.image</string></array>
    </dict>
  </array>
</dict>
</plist>
PLIST

/System/Library/CoreServices/pbs -flush >/dev/null 2>&1 || true

echo "installed: $NAME"
echo "  Finder -> right-click an image -> Quick Actions -> $NAME"
echo "  uninstall: macos/install.sh --uninstall"
