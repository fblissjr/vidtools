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

# -x above proves a file is executable, not that it runs. An editable install
# whose package directory has gone missing leaves the console script in place
# and failing on every call, and installing over that printed "installed:" and
# deferred the failure to right-click time, where the only symptom was an
# Automator dialog with an empty message.
if ! "$BIN" --help >/dev/null 2>&1; then
    echo "error: the vidtools binary is present but does not run." >&2
    echo "       Run 'uv sync' (or 'uv sync --reinstall-package vidtools') first." >&2
    exit 1
fi

# The venv is a hard precondition by this point, so its interpreter is the one
# guaranteed to exist. Taking python3 from PATH instead binds the install to
# whatever happens to be first there, which is not necessarily the same Python
# -- or present at all.
PYTHON="$REPO_ROOT/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="$REPO_ROOT/.venv/bin/python"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$BUNDLE"
    echo "removed: $NAME"
    exit 0
fi

# Build into a staging directory and swap it in, rather than deleting the
# installed bundle and generating in its place. The old order destroyed a
# working Quick Action before it knew whether the replacement would build, so
# any failure during generation left nothing installed and no way back.
#
# The swap is still a replace, not an overwrite: rewriting files inside an
# existing bundle leaves the bundle *directory's* mtime untouched, so macOS
# keeps its cached registration and the action stops appearing under System
# Settings -> Login Items & Extensions -> Finder, even though the services DB
# still has it and the right-click menu still works. A fresh directory forces a
# re-scan. Staging alongside the target keeps the swap on one filesystem.
mkdir -p "$SERVICES"
STAGE=$(mktemp -d "$SERVICES/.$NAME.staging.XXXXXX")
PREVIOUS="$SERVICES/.$NAME.previous.$$"

# The trap has to stay armed through the swap, not just through generation.
# Deleting the installed bundle outright and then moving the new one in looks
# atomic but is not: if the delete fails partway -- one immutable or locked file
# is enough -- the script aborts having already gutted the old bundle, and a
# trap that only removed $STAGE would throw away the good replacement too. That
# is strictly worse than the delete-then-generate order this replaced, which at
# least had no finished build to lose.
#
# So the old bundle is moved aside rather than deleted, and cleanup restores it
# if the bundle slot ended up empty. Ordering matters: restore before removing
# the backup, and never let cleanup failures mask the real exit status.
cleanup() {
    rm -rf "$STAGE" 2>/dev/null || true
    if [ -d "$PREVIOUS" ] && [ ! -e "$BUNDLE" ]; then
        mv "$PREVIOUS" "$BUNDLE" 2>/dev/null || true
    fi
    rm -rf "$PREVIOUS" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$STAGE/Contents"
SCRIPT_SRC="$REPO_ROOT/macos/compress-image.sh"

BIN="$BIN" SCRIPT_SRC="$SCRIPT_SRC" NAME="$NAME" \
    "$PYTHON" "$REPO_ROOT/macos/make_workflow.py" \
    "$STAGE/Contents/document.wflow" "$STAGE/Contents/Info.plist"

# Renaming the old bundle away is what makes the swap recoverable. A rename
# succeeds even when the directory holds a file that cannot be deleted, so the
# unremovable-file case ends with the new action installed rather than none.
if [ -e "$BUNDLE" ]; then
    mv "$BUNDLE" "$PREVIOUS"
fi
mv "$STAGE" "$BUNDLE"


/System/Library/CoreServices/pbs -flush >/dev/null 2>&1 || true

echo "installed: $NAME"
echo "  Finder -> right-click an image -> Quick Actions -> $NAME"
echo "  uninstall: macos/install.sh --uninstall"
