#!/bin/zsh
# Quick Action body: compress the selected images via vidtools.
#
# Installed into the user's Services folder by macos/install.sh, which
# substitutes the real binary path for @VIDTOOLS_BIN@. That placeholder is why
# this file can live in the repo -- an absolute path baked in here would be a
# path leak.
#
# Design notes:
#
# * One Finder entry, parameterised by a dialog, rather than one entry per
#   operation. That is the convention macOS's own Convert Image follows, and it
#   keeps the Quick Actions menu from filling up.
# * Presets combine format, size and metadata into a single choice. AppleScript
#   cannot draw a multi-control panel, and three sequential prompts to compress
#   a file is worse than one list of sensible outcomes.
# * PATH is minimal under Automator, so every binary is called by absolute path.
# * zsh, not bash. /bin/bash on macOS is still 3.2.57, where `set -u` plus an
#   empty-array expansion is a fatal "unbound variable" -- which would have hit
#   exactly the one preset that passes no flags. zsh 5.9 is the platform default
#   and handles it. Verified on macOS 26.5.2.

set -uo pipefail

VIDTOOLS="@VIDTOOLS_BIN@"
OSASCRIPT=/usr/bin/osascript

# " and \ are syntax inside an AppleScript string literal. An unescaped one does
# not garble the dialog -- it makes the whole -e argument a syntax error, so
# nothing is displayed at all. That is the worst available failure for the code
# path whose only job is reporting failure, and it became reachable the moment
# these messages started carrying error text and filenames rather than literals.
# A raw newline is fine unescaped; "\n" is not, since the backslash is escaped
# here before AppleScript ever sees it.
_as_quote() {
    printf '%s' "$1" | /usr/bin/sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

notify() {
    "$OSASCRIPT" -e \
        "display notification \"$(_as_quote "$2")\" with title \"$(_as_quote "$1")\"" \
        >/dev/null 2>&1
}

fail() {
    "$OSASCRIPT" -e \
        "display alert \"vidtools\" message \"$(_as_quote "$1")\" as critical" \
        >/dev/null 2>&1
    exit 1
}

# -x proves the file is executable, not that it runs. A venv whose package
# directory has gone missing still satisfies -x, and exits 1 on every call --
# which is how this action came to report "Could not compress 1 file(s)" with
# no cause attached. Check that it actually starts, and say what to do.
[ -x "$VIDTOOLS" ] || fail "vidtools binary not found. Re-run macos/install.sh from the repo."
"$VIDTOOLS" --help >/dev/null 2>&1 || fail \
    "vidtools is installed but will not run. Run 'uv sync' in the repo, then re-run macos/install.sh."

[ "$#" -gt 0 ] || exit 0

# Preset labels are outcome-shaped ("Smallest", "Visually lossless") rather than
# flag-shaped, because the person right-clicking a file is choosing a result.
CHOICE=$("$OSASCRIPT" <<'APPLESCRIPT'
set options to {¬
    "Smallest - sized for AI vision (WebP)", ¬
    "Smallest - sized for AI vision (AVIF, not readable by AI tools)", ¬
    "Balanced (WebP)", ¬
    "Visually lossless (WebP)", ¬
    "Lossless - exact pixels", ¬
    "Balanced, keep metadata"}
set picked to choose from list options ¬
    with title "Compress Image" ¬
    with prompt "How should these be compressed?" ¬
    default items {"Smallest - sized for AI vision (WebP)"}
if picked is false then return "CANCELLED"
return item 1 of picked
APPLESCRIPT
)

if [ "$CHOICE" = "CANCELLED" ] || [ -z "$CHOICE" ]; then
    exit 0
fi

ARGS=()
case "$CHOICE" in
    "Smallest - sized for AI vision (WebP)")  ARGS=(--fit vision) ;;
    "Smallest - sized for AI vision (AVIF"*)  ARGS=(--fit vision --allow-avif) ;;
    "Balanced (WebP)")                        ARGS=() ;;
    "Visually lossless (WebP)")               ARGS=(--target-ssim 0.995) ;;
    "Lossless - exact pixels")                ARGS=(--lossless) ;;
    "Balanced, keep metadata")                ARGS=(--preserve-metadata) ;;
    *) exit 0 ;;
esac

ok=0
failed=0
total_before=0
total_after=0
first_error=""

for f in "$@"; do
    [ -f "$f" ] || continue
    dir=$(/usr/bin/dirname "$f")
    base=$(/usr/bin/basename "$f")
    stem="${base%.*}"

    # No extension on the output: the winning format supplies one, which is the
    # whole point of letting the search choose.
    out="$dir/${stem}_compressed"

    before=$(/usr/bin/stat -f%z "$f" 2>/dev/null || echo 0)
    # Keep stderr rather than discarding it. Every diagnostic this tool emits on
    # failure goes there, and routing it to /dev/null is why a dead venv reached
    # the user as an alert naming no cause.
    errfile=$(/usr/bin/mktemp -t vidtools-qa)
    # Ask where it wrote rather than rediscovering it. `compress` chooses the
    # extension, so the output path is not predictable from the input, and
    # globbing for it picks up a stale file from an earlier run that chose a
    # different format -- reporting a saving against the wrong file.
    if report=$("$VIDTOOLS" compress "${ARGS[@]}" "$f" "$out" 2>"$errfile"); then
        written=${${(M)${(f)report}:#wrote *}#wrote }
        after=$(/usr/bin/stat -f%z "$written" 2>/dev/null || echo 0)
        ok=$((ok + 1))
        total_before=$((total_before + before))
        total_after=$((total_after + after))
    else
        failed=$((failed + 1))
        # Keep the first cause only. A dialog can carry one usefully, and a
        # batch that fails generally fails the same way for every file.
        if [ -z "$first_error" ]; then
            first_error=$(/usr/bin/tail -n 3 "$errfile" | /usr/bin/tr '\n' ' ')
        fi
    fi
    /bin/rm -f "$errfile"
done

if [ "$ok" -eq 0 ]; then
    summary="Could not compress ${failed} file(s)."
    if [ -n "$first_error" ]; then
        summary="$summary
$first_error"
    fi
    fail "$summary"
fi

if [ "$total_after" -gt 0 ]; then
    ratio=$(echo "scale=1; $total_before / $total_after" | /usr/bin/bc)
    msg="${ok} file(s), ${ratio}x smaller"
else
    msg="${ok} file(s)"
fi
if [ "$failed" -gt 0 ]; then
    msg="$msg (${failed} failed)"
fi

notify "Compressed" "$msg"
