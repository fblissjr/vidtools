#!/bin/bash
# Build and install the vidtools Finder Quick Actions:
#
#   Compress Image  -- right-click images  (macos/compress-image.sh)
#   Compress Video  -- right-click videos  (macos/compress-video.sh)
#
# Two bundles rather than one accepting both types. Finder then offers each
# entry only on files it can act on, and neither script has to cope with a
# mixed selection -- which has no correct preset list.
#
# The bundles are generated rather than checked in, for two reasons: the scripts
# inside them need this machine's absolute paths (to the vidtools binary, and
# for the video action to ffmpeg's directory), neither of which may appear in
# the repo, and a .wflow is an opaque plist that reviews badly. Generating keeps
# the reviewable artifacts -- the shell scripts -- in plain sight.
#
# Automator rather than a SwiftUI app: a signed app is a notarisation problem
# this project does not otherwise have, and the Run Shell Script action covers
# the case. The cost is that AppleScript cannot draw a multi-control panel, so
# each dialog is one preset list rather than a Format / Size / Preserve Metadata
# trio. If that trade stops being worth it, the scripts here are what a native
# front end would call.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Overridable so tests can install into a temp dir instead of the real Services
# folder. Nothing else should set it.
SERVICES="${VIDTOOLS_SERVICES_DIR:-$HOME/Library/Services}"   # path-privacy: ignore -- the OS decides where Quick Actions live

IMAGE_NAME="Compress Image"
VIDEO_NAME="Compress Video"

# Remove one of the bundle-shaped directories this script created -- an
# installed bundle, a staging dir, or a moved-aside backup. All three hold the
# same two generated files and nothing else.
#
# Deliberately NOT `rm -rf`. We know the exact contents, so recursion buys
# nothing and only widens what a wrong path could destroy. `rmdir` refuses any
# directory that still holds something, so this physically cannot cascade: an
# unexpected file leaves the directory standing and is reported, rather than
# being deleted sight-unseen. Reversing that trade would mean this script could
# erase a bundle a user had put their own files in, to save one call.
#
# Returns non-zero if anything was left behind.
remove_generated_bundle() {
    # `${1:-}` rather than `$1`: an unbound positional under `set -u` would
    # abort the caller, and one caller is the EXIT trap -- the same
    # trap-dies-silently failure this file already had once.
    local dir="${1:-}"

    [ -n "$dir" ] || return 0
    [ -e "$dir" ] || return 0
    # Only ever a directory. Anything else standing where a bundle belongs was
    # not created by this script, so it is not this script's to delete -- and
    # returning 0 for it would report a removal that never happened.
    [ -d "$dir" ] || return 1

    # Nothing outside the Services directory we were pointed at, ever. Cheap
    # insurance against a future edit that makes one of these paths computable
    # to something else.
    case "$dir" in
        "$SERVICES"/*) ;;
        *)
            echo "refusing to remove '$dir': outside '$SERVICES'" >&2
            return 1
            ;;
    esac

    # The two files make_workflow.py writes, plus the one stray macOS is
    # entitled to leave if the folder has been opened in Finder.
    rm -f "$dir/Contents/document.wflow" \
          "$dir/Contents/Info.plist" \
          "$dir/Contents/.DS_Store" \
          "$dir/.DS_Store" 2>/dev/null || true
    rmdir "$dir/Contents" 2>/dev/null || true
    rmdir "$dir" 2>/dev/null || true

    [ ! -e "$dir" ]
}

if [ "${1:-}" = "--uninstall" ]; then
    rc=0
    for name in "$IMAGE_NAME" "$VIDEO_NAME"; do
        bundle="$SERVICES/$name.workflow"
        if [ ! -e "$bundle" ]; then
            echo "not installed: $name"
        elif remove_generated_bundle "$bundle"; then
            echo "removed: $name"
        else
            # Covers both shapes of "not ours": a bundle holding extra files,
            # and something that is not a bundle directory at all.
            echo "warning: '$bundle' is not a bundle this script can safely remove." >&2
            echo "         Left in place -- delete it by hand if you meant to." >&2
            rc=1
        fi
    done
    exit "$rc"
fi

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

# Resolve ffmpeg's directory now and bake it into the video action's PATH.
# Automator's PATH holds neither /usr/local/bin nor Homebrew's prefix, and every
# video preset shells out to ffmpeg on every file. (The image action needs none
# of this -- `compress` is pure PIL.)
#
# The directory of the symlink, deliberately not its target: a Homebrew bin
# entry survives version bumps, the Cellar path it points into does not.
FFMPEG_DIR=""
if ffmpeg_bin=$(command -v ffmpeg 2>/dev/null); then
    FFMPEG_DIR=$(cd "$(dirname "$ffmpeg_bin")" && pwd)
fi

# Per-action state, read by the EXIT trap. Cleared after each success so a
# failure in the second action cannot roll back the first.
STAGE=""
PREVIOUS=""
BUNDLE=""

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
# Every step is `|| true`. `set -e` applies to the last command of an AND-list,
# so `[ -n "$STAGE" ] && rm -rf "$STAGE"` aborts this whole function the moment
# the rm fails -- and a failing rm is precisely the case this exists for, so the
# restore below would be skipped exactly when it is needed. That also masks the
# real exit status.
cleanup() {
    remove_generated_bundle "$STAGE" || true
    if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ] && [ -n "$BUNDLE" ] && [ ! -e "$BUNDLE" ]; then
        mv "$PREVIOUS" "$BUNDLE" 2>/dev/null || true
    fi
    remove_generated_bundle "$PREVIOUS" || true
    true
}
trap cleanup EXIT

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
install_action() {
    local name="$1" script="$2" kind="$3"

    BUNDLE="$SERVICES/$name.workflow"
    STAGE=$(mktemp -d "$SERVICES/.$name.staging.XXXXXX")
    PREVIOUS="$SERVICES/.$name.previous.$$"
    mkdir -p "$STAGE/Contents"

    BIN="$BIN" SCRIPT_SRC="$REPO_ROOT/macos/$script" NAME="$name" KIND="$kind" \
        FFMPEG_DIR="$FFMPEG_DIR" \
        "$PYTHON" "$REPO_ROOT/macos/make_workflow.py" \
        "$STAGE/Contents/document.wflow" "$STAGE/Contents/Info.plist"

    # Renaming the old bundle away is what makes the swap recoverable. A rename
    # succeeds even when the directory holds a file that cannot be deleted, so
    # the unremovable-file case ends with the new action installed rather than
    # none.
    if [ -e "$BUNDLE" ]; then
        mv "$BUNDLE" "$PREVIOUS"
    fi
    mv "$STAGE" "$BUNDLE"

    STAGE=""
    # Say so rather than swallowing it. Not fatal -- the new action is already
    # installed by this point -- but a backup that cannot be removed would
    # otherwise pile up in the Services folder with nothing to explain it.
    if ! remove_generated_bundle "$PREVIOUS"; then
        echo "note: kept '$PREVIOUS'; it holds files this script did not create." >&2
    fi
    PREVIOUS=""
    BUNDLE=""

    echo "installed: $name"
}

mkdir -p "$SERVICES"

install_action "$IMAGE_NAME" compress-image.sh image

# Skip rather than refuse. The image action has no ffmpeg dependency at all, so
# blocking it over the video action's prerequisite punishes the wrong user --
# and installing a video action with no ffmpeg to call ships a right-click that
# errors on every file.
if [ -n "$FFMPEG_DIR" ]; then
    install_action "$VIDEO_NAME" compress-video.sh video
else
    echo "warning: ffmpeg not found on PATH -- skipping '$VIDEO_NAME'." >&2
    echo "         Install it ('brew install ffmpeg-full'), then re-run this script." >&2
fi

/System/Library/CoreServices/pbs -flush >/dev/null 2>&1 || true

echo
echo "  Finder -> right-click a file -> Quick Actions"
echo "  uninstall: macos/install.sh --uninstall"
