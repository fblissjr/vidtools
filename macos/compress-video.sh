#!/bin/zsh
# Quick Action body: shrink the selected videos via vidtools.
#
# Installed into the user's Services folder by macos/install.sh, which
# substitutes real paths for @VIDTOOLS_BIN@ and @FFMPEG_DIR@. Those placeholders
# are why this file can live in the repo -- absolute paths baked in here would
# be path leaks.
#
# The image action (macos/compress-image.sh) is the sibling to read first; this
# one follows its shape deliberately. Where it differs, it differs for a reason:
#
# * PATH. `compress` is pure PIL, so the image action never needed anything on
#   PATH. Every preset here shells out to ffmpeg on every file, and Automator
#   runs with a minimal PATH holding neither /usr/local/bin nor Homebrew's.
#   The directory is resolved at install time and prepended below.
# * Output naming is deterministic, so there is no `wrote ...` line to parse.
#   Re-encodes always produce .mp4; the stream-copy preset keeps the source
#   container, because copying an arbitrary codec into mp4 is not always legal.
# * Encoding is written to a temp file in the destination directory and moved
#   into place only on success. Video encodes take real time and can be killed
#   half-written, and this is also what makes a stale output from an earlier run
#   impossible to measure -- the bug the image action shipped and then fixed by
#   parsing stdout instead.
#
# libx264 with CRF rather than VideoToolbox. Measured on an M2 Ultra with
# ffmpeg 8.1.2, 20s of 1080p down to 720p: x264 took 1.4s for 1.53 MB,
# h264_videotoolbox 0.9s for 5.07 MB. Hardware encoding buys almost nothing at
# this scale and costs 3x the file size, and CRF holds quality steady across
# content where a fixed bitrate cannot.

set -uo pipefail

VIDTOOLS="@VIDTOOLS_BIN@"
FFMPEG_DIR="@FFMPEG_DIR@"
OSASCRIPT=/usr/bin/osascript

# Guarded rather than unconditional: prepending an empty string leaves an empty
# PATH component, which means "the current directory" -- a worse outcome than
# the missing-ffmpeg error below.
if [ -n "$FFMPEG_DIR" ]; then
    export PATH="$FFMPEG_DIR:$PATH"
fi

# " and \ are syntax inside an AppleScript string literal. An unescaped one does
# not garble the dialog -- it makes the whole -e argument a syntax error, so
# nothing is displayed at all. That is the worst available failure for the code
# path whose only job is reporting failure, and it is reachable here because
# these messages carry ffmpeg's error output and user filenames.
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
# directory has gone missing still satisfies -x and exits 1 on every call --
# which is how the image action came to report a failure with no cause attached.
[ -x "$VIDTOOLS" ] || fail "vidtools binary not found. Re-run macos/install.sh from the repo."
"$VIDTOOLS" --help >/dev/null 2>&1 || fail \
    "vidtools is installed but will not run. Run 'uv sync' in the repo, then re-run macos/install.sh."

# The ffmpeg directory is baked in at install time, so it goes stale if ffmpeg
# is later moved, upgraded into a different prefix, or unlinked. Check it here
# rather than letting every file fail one at a time with the same cause.
command -v ffmpeg >/dev/null 2>&1 || fail \
    "ffmpeg not found. Install it ('brew install ffmpeg-full'), then re-run macos/install.sh."

[ "$#" -gt 0 ] || exit 0

# Preset labels are outcome-shaped ("Smallest", "Balanced") rather than
# flag-shaped, because the person right-clicking a file is choosing a result.
# The resolutions are caps, not targets: a clip already smaller than the preset
# is never enlarged (see --max-edge).
CHOICE=$("$OSASCRIPT" <<'APPLESCRIPT'
set options to {¬
    "Smallest - 480p", ¬
    "Small - 720p", ¬
    "Balanced - 1080p", ¬
    "Original size, just compress", ¬
    "Strip metadata only - no re-encode", ¬
    "Balanced - 1080p, keep metadata"}
set picked to choose from list options ¬
    with title "Compress Video" ¬
    with prompt "How should these be compressed?" ¬
    default items {"Small - 720p"}
if picked is false then return "CANCELLED"
return item 1 of picked
APPLESCRIPT
)

if [ "$CHOICE" = "CANCELLED" ] || [ -z "$CHOICE" ]; then
    exit 0
fi

# MODE decides both the command shape and the output naming. PRE holds flags
# that must precede the input path: `pipeline` takes its steps as
# argparse.REMAINDER, so anything after the output filename is parsed as a step.
MODE=pipeline
PRE=()
STEPS=()
SUFFIX=_compressed
case "$CHOICE" in
    "Smallest - 480p")
        STEPS=(resize --max-edge 854 convert --crf 28) ;;
    "Small - 720p")
        STEPS=(resize --max-edge 1280 convert --crf 26) ;;
    "Balanced - 1080p")
        STEPS=(resize --max-edge 1920 convert --crf 24) ;;
    "Original size, just compress")
        STEPS=(convert --crf 24) ;;
    "Strip metadata only - no re-encode")
        MODE=copy
        SUFFIX=_stripped ;;
    "Balanced - 1080p, keep metadata")
        PRE=(--preserve-metadata)
        STEPS=(resize --max-edge 1920 convert --crf 24) ;;
    *) exit 0 ;;
esac

# Re-encoding a long clip takes minutes with no progress indication beyond
# Automator's menu-bar gear. One notification up front is the difference between
# "working" and "did my right-click do anything". The stream-copy preset is
# effectively instant, so it would only produce two notifications in a row.
if [ "$MODE" = pipeline ]; then
    notify "Compressing" "$# file(s)..."
fi

ok=0
failed=0
total_before=0
total_after=0
first_error=""

# Paths this run must not write over. Seeded with the whole selection, because
# an output can legitimately land on a file the user also selected: pick
# `clip.mp4` together with an existing `clip_compressed.mp4` -- the obvious move
# when re-compressing a folder -- and the first iteration's output destroys the
# second iteration's input before it is ever read.
#
# It also covers the same-stem case. Every re-encode preset forces .mp4, so
# `clip.mov` and `clip.mp4` both resolve to `clip_compressed.mp4`; without this
# the second silently overwrites the first while both are still counted.
CLAIMED=("$@")

_claimed() {
    local c
    for c in "${CLAIMED[@]}"; do
        [ "$c" = "$1" ] && return 0
    done
    return 1
}

for f in "$@"; do
    [ -f "$f" ] || continue
    dir=$(/usr/bin/dirname "$f")
    base=$(/usr/bin/basename "$f")
    stem="${base%.*}"

    if [ "$MODE" = copy ]; then
        # Keep the source container: a stream copy into mp4 is only legal for
        # codecs mp4 can carry, and the point of this preset is to touch nothing
        # but the metadata. A file with no extension falls back to mp4, which
        # covers everything Finder classifies as public.movie in practice.
        ext="${base##*.}"
        [ "$ext" = "$base" ] && ext=mp4
    else
        ext=mp4
    fi
    # Same rule and same spelling as `compress._unique_path`: a derived output
    # never replaces an existing file, and the counter starts at 2 so the second
    # result reads as "-2". Checking the selection as well as the disk covers
    # the case a file-existence test cannot -- two inputs in this batch whose
    # outputs collide before either has been written.
    out="$dir/${stem}${SUFFIX}.$ext"
    n=2
    while [ -e "$out" ] || _claimed "$out"; do
        out="$dir/${stem}${SUFFIX}-$n.$ext"
        n=$((n + 1))
    done
    CLAIMED+=("$out")

    before=$(/usr/bin/stat -f%z "$f" 2>/dev/null || echo 0)
    # Keep stderr rather than discarding it. Every diagnostic vidtools emits on
    # failure goes there, and routing it to /dev/null is why a dead venv once
    # reached the user as an alert naming no cause.
    errfile=$(/usr/bin/mktemp -t vidtools-qa)

    # Encode beside the destination, then move into place. A half-written file
    # from an interrupted encode never appears under the real name, and a stale
    # output from an earlier run can never be measured as if it were this one's.
    tmp="$dir/.${stem}${SUFFIX}.$$.$ext"

    if [ "$MODE" = copy ]; then
        "$VIDTOOLS" convert "$f" "$tmp" --copy 2>"$errfile"
    else
        "$VIDTOOLS" pipeline "${PRE[@]}" "$f" "$tmp" "${STEPS[@]}" 2>"$errfile"
    fi
    # Captured immediately. A bare `$?` after `fi` happens to carry the branch's
    # exit code, but it is one inserted line away from silently meaning
    # something else, in the test that decides whether a file was compressed.
    #
    # Named `rc`, not `status`: `status` is read-only in zsh (it is the spelled
    # -out form of `$?`), and assigning to it aborts the script outright.
    rc=$?

    # Require a non-empty file, not just exit 0. ffmpeg has more than one way to
    # exit clean having written nothing, and counting the exit code alone would
    # make `ok` mean "the process returned" rather than "a video came out".
    if [ "$rc" -eq 0 ] && [ -s "$tmp" ] && /bin/mv -f "$tmp" "$out" 2>/dev/null; then
        after=$(/usr/bin/stat -f%z "$out" 2>/dev/null || echo 0)
        ok=$((ok + 1))
        total_before=$((total_before + before))
        total_after=$((total_after + after))
    else
        failed=$((failed + 1))
        /bin/rm -f "$tmp"
        # Keep the first cause only. A dialog can carry one usefully, and a
        # batch that fails generally fails the same way for every file.
        #
        # `tail -n 3` alone is not enough here, and this is the one place the
        # video action genuinely differs from the image one. `vidtools pipeline`
        # installs a tqdm sink that redraws with carriage returns, so a real
        # failing run leaves ~1.3 KB of stderr containing a single newline --
        # the whole progress bar is one "line", and tailing lines returns the
        # bar with the cause pushed past what the dialog shows. So: split on
        # CR as well, drop the bar frames (tqdm's `NN%|` is unmistakable and
        # cannot occur in an ffmpeg diagnostic), then take the tail.
        if [ -z "$first_error" ]; then
            first_error=$(/usr/bin/tr '\r' '\n' <"$errfile" \
                | /usr/bin/grep -v '%|' \
                | /usr/bin/grep -v '^[[:space:]]*$' \
                | /usr/bin/tail -n 3 | /usr/bin/tr '\n' ' ')
        fi
    fi
    /bin/rm -f "$errfile"
done

# Nothing selected was a regular file -- every entry was a directory, a broken
# alias, or something deleted between the right-click and the run. Both counters
# are still zero, so the failure branch below would fire a *critical* alert
# reading "Could not compress 0 file(s)". Say nothing instead.
if [ "$ok" -eq 0 ] && [ "$failed" -eq 0 ]; then
    exit 0
fi

if [ "$ok" -eq 0 ]; then
    summary="Could not compress ${failed} file(s)."
    if [ -n "$first_error" ]; then
        summary="$summary
$first_error"
    fi
    fail "$summary"
fi

if [ "$MODE" = copy ]; then
    msg="${ok} file(s), metadata removed"
elif [ "$total_after" -gt 0 ]; then
    ratio=$(echo "scale=1; $total_before / $total_after" | /usr/bin/bc)
    msg="${ok} file(s), ${ratio}x smaller"
else
    msg="${ok} file(s)"
fi
if [ "$failed" -gt 0 ]; then
    msg="$msg (${failed} failed)"
fi

notify "Compressed" "$msg"
