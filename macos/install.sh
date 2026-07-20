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
# Overridable so tests can install into a temp dir instead of the real Services
# folder. Nothing else should set it.
SERVICES="${VIDTOOLS_SERVICES_DIR:-$HOME/Library/Services}"   # path-privacy: ignore -- the OS decides where Quick Actions live
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

# Replace the bundle rather than overwriting in place. `mkdir -p` + rewriting
# the files leaves the bundle *directory's* mtime untouched, so macOS keeps its
# cached registration and the action stops appearing under System Settings ->
# Login Items & Extensions -> Finder, even though the services DB still has it
# and the right-click menu still works. A fresh directory forces a re-scan.
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents"
SCRIPT_SRC="$REPO_ROOT/macos/compress-image.sh"

BIN="$BIN" SCRIPT_SRC="$SCRIPT_SRC" NAME="$NAME" \
    python3 "$REPO_ROOT/macos/make_workflow.py" \
    "$BUNDLE/Contents/document.wflow" "$BUNDLE/Contents/Info.plist"


/System/Library/CoreServices/pbs -flush >/dev/null 2>&1 || true

echo "installed: $NAME"
echo "  Finder -> right-click an image -> Quick Actions -> $NAME"
echo "  uninstall: macos/install.sh --uninstall"
