"""The video Quick Action shell script, driven end-to-end with stubs.

A sibling of `test_macos_quick_action.py`, which covers the image script. They
are separate files because the scripts are separate installed artifacts with
different contracts -- this one shells out to ffmpeg, writes through a temp
file, and has two output-naming schemes. The shared claims (preset mapping,
hostile filenames, cancellation, failure reporting) are re-asserted rather than
imported: the image script's history is that each of its five defects lived in
a different layer, and a shared harness would have to be right about all of
them at once.

`vidtools`, `osascript` and `ffmpeg` are stubbed.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not Path("/bin/zsh").exists(),
    reason="needs macOS zsh and BSD stat",
)

SCRIPT = Path(__file__).resolve().parents[1] / "macos" / "compress-video.sh"

# Every preset, and the exact recipe / flag argv it must produce before the
# input/output pair.
PRESETS = {
    "Universal <100 MB (H.264 - Sharing / Web)": ["--recipe", "universal"],
    "Max Quality <100 MB (10-bit HEVC - Apple / Modern)": ["--recipe", "hevc"],
    "Fast Hardware <100 MB (Apple Silicon VideoToolbox)": ["--recipe", "hw"],
    "Visually Lossless Archival (10-bit HEVC CRF 16)": ["--recipe", "archival"],
    "Balanced - 1080p": ["--recipe", "balanced-1080p"],
    "Small - 720p": ["--recipe", "small-720p"],
    "Smallest - 480p": ["--recipe", "smallest-480p"],
    "Original size, just compress": ["--recipe", "crf"],
    "Balanced - 1080p, keep metadata":
        ["--recipe", "balanced-1080p", "--preserve-metadata"],
}

VIDTOOLS_OK = (
    "#!/bin/zsh\n"
    'if [ "$1" = "--help" ]; then exit 0; fi\n'
    'print -r -- "$@" >> "{argfile}"\n'
    # Find the output by name rather than by position -- `pipeline` puts steps
    # after it, and an optional flag can precede the input. Matching the suffix
    # rather than the temp marker is deliberate: a stub keyed to the temp name
    # goes silently inert if the temp indirection is ever removed, which is
    # exactly how the first version of this file failed to notice.
    'for a in "$@"; do case "$a" in *_compressed*|*_stripped*) out="$a";; esac; done\n'
    'printf "padding padding padding padding" > "$out"\n'
)


@pytest.fixture
def harness(tmp_path):
    """Return (run, argfile, told). `run(choice, *files)` executes the script."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argfile = tmp_path / "args.txt"
    notify = tmp_path / "notify.txt"

    vt = bin_dir / "vidtools"
    vt.write_text(VIDTOOLS_OK.format(argfile=argfile))
    vt.chmod(0o755)

    # The script refuses to run without ffmpeg on PATH, so the stub directory
    # has to supply one. That refusal is itself asserted further down.
    ff = bin_dir / "ffmpeg"
    ff.write_text("#!/bin/zsh\nexit 0\n")
    ff.chmod(0o755)

    # zsh sources its per-user env file for *every* invocation, scripts
    # included, and a dotfile setup that composes PATH there silently restored
    # a full PATH underneath the two tests that assert ffmpeg is absent.
    # Pointing ZDOTDIR at an empty directory is what makes a restricted PATH
    # actually restricted.
    zdotdir = tmp_path / "zdotdir"
    zdotdir.mkdir()

    def run(choice, *files, vt_body=None, ffmpeg_dir=None):
        if vt_body is not None:
            vt.write_text(vt_body)
            vt.chmod(0o755)
        osa = bin_dir / "osascript"
        osa.write_text(
            "#!/bin/zsh\n"
            'if [ "$1" = "-e" ]; then\n'
            f'  print -r -- "$2" >> "{notify}"\n'
            "  exit 0\n"
            "fi\n"
            f"print -r -- {choice!r}\n"
        )
        osa.chmod(0o755)

        body = SCRIPT.read_text().replace("@VIDTOOLS_BIN@", str(vt))
        body = body.replace(
            "@FFMPEG_DIR@", str(bin_dir) if ffmpeg_dir is None else ffmpeg_dir)
        body = body.replace("OSASCRIPT=/usr/bin/osascript", f"OSASCRIPT={osa}")
        runner = tmp_path / "run.sh"
        runner.write_text(body)
        runner.chmod(0o755)
        # Execute by path so the script's own shebang selects the interpreter.
        # Forcing /bin/zsh would mask a regression to /bin/bash, which is 3.2.57
        # on macOS and dies expanding an empty array under `set -u` -- the exact
        # defect the image action shipped, and reachable here through PRE=().
        return subprocess.run([str(runner), *map(str, files)],
                              capture_output=True, text=True,
                              env={**os.environ, "PATH": "/usr/bin:/bin",
                                   "ZDOTDIR": str(zdotdir)})

    def told():
        return notify.read_text() if notify.exists() else ""

    return run, argfile, told


def _video(tmp_path, name="clip.mp4", size=8192):
    p = tmp_path / name
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * size)
    return p


@pytest.mark.parametrize("choice,flags", PRESETS.items(), ids=lambda v: str(v)[:26])
def test_every_reencode_preset_maps_to_its_recipe(harness, tmp_path, choice, flags):
    """Claim: deleting these lets a preset silently fall through to the default.

    Includes presets that leave EXTRA_ARGS=() empty -- the shape that killed the
    image action's no-flag preset under bash 3.2."""
    run, argfile, _ = harness
    r = run(choice, _video(tmp_path))
    assert r.returncode == 0, r.stderr
    recorded = argfile.read_text().split()
    assert recorded[0] == "compress"
    assert recorded[1:1 + len(flags)] == flags


@pytest.mark.parametrize(
    "choice,flags",
    [(k, v) for k, v in PRESETS.items() if "keep metadata" not in k],
    ids=lambda v: str(v)[:26],
)
def test_keep_metadata_checkbox_appends_preserve_metadata_to_every_preset(
    harness, tmp_path, choice, flags
):
    """The AppKit NSAlert suppression checkbox appends `|KEEP_META` to the chosen
    preset title, allowing any preset to keep metadata without duplicating menu items."""
    run, argfile, _ = harness
    r = run(f"{choice}|KEEP_META", _video(tmp_path))
    assert r.returncode == 0, r.stderr
    argv = argfile.read_text().split()
    assert argv[0] == "compress"
    assert argv[1:1 + len(flags) + 1] == [*flags, "--preserve-metadata"]


def test_strip_only_preset_stream_copies_instead_of_re_encoding(harness, tmp_path):
    """Claim: deleting this turns the one instant preset into a full re-encode."""
    run, argfile, _ = harness
    r = run("Strip metadata only - no re-encode", _video(tmp_path))
    assert r.returncode == 0, r.stderr
    argv = argfile.read_text().split()
    assert argv[0] == "compress"
    assert argv[1:3] == ["--recipe", "copy"]


def test_strip_only_keeps_the_source_container(harness, tmp_path):
    """Claim: deleting this makes the stream-copy preset write .mp4 regardless.

    A copy into mp4 is only legal for codecs mp4 can carry; a .mkv holding
    anything else fails outright. The preset is supposed to touch nothing but
    the metadata, so the container has to survive too."""
    run, _, _ = harness
    run("Strip metadata only - no re-encode", _video(tmp_path, "movie.mkv"))
    assert (tmp_path / "movie_stripped.mkv").exists(), \
        f"produced {sorted(p.name for p in tmp_path.iterdir())}"


def test_reencode_presets_always_write_mp4(harness, tmp_path):
    run, _, _ = harness
    run("Small - 720p", _video(tmp_path, "movie.mkv"))
    assert (tmp_path / "movie_compressed.mp4").exists()


def test_the_encode_target_is_not_the_final_output_path(harness, tmp_path):
    """Claim: deleting the temp-file indirection lets ffmpeg write directly onto
    the destination, so a half-written file sits under the real name for the
    whole encode.

    Asserted on the argv rather than through a failure, because that is the
    property itself: an interrupted encode cannot be simulated by a stub that
    returns, and every observable *consequence* of dropping the indirection is
    also reachable another way -- which is how the first version of this test
    passed while blind to the defect."""
    run, argfile, _ = harness
    r = run("Small - 720p", _video(tmp_path))
    assert r.returncode == 0, r.stderr
    argv = argfile.read_text().split()
    final = str(tmp_path / "clip_compressed.mp4")
    assert final not in argv, "encoded straight onto the final output path"
    assert (tmp_path / "clip_compressed.mp4").exists(), "and yet it must end up there"


def test_a_failed_encode_leaves_no_partial_file(harness, tmp_path):
    """The observable half: whatever the encoder managed to write is discarded
    rather than left lying next to the original looking like a result."""
    run, _, _ = harness
    vt = ("#!/bin/zsh\n"
          'if [ "$1" = "--help" ]; then exit 0; fi\n'
          'for a in "$@"; do case "$a" in *_compressed*) out="$a";; esac; done\n'
          'printf "truncated" > "$out"\n'      # wrote something...
          "exit 1\n")                          # ...then died
    r = run("Small - 720p", _video(tmp_path), vt_body=vt)
    assert r.returncode == 1
    leftovers = [p.name for p in tmp_path.iterdir() if "clip_compressed" in p.name]
    assert leftovers == [], f"partial file left behind: {leftovers}"


def test_exit_zero_without_an_output_file_counts_as_failure(harness, tmp_path):
    """Claim: deleting the `-s` check lets `ok` mean "the process returned"
    rather than "a video came out".

    This is not hypothetical for ffmpeg -- `screenshot` shipped it: seeking past
    the end of the input makes the image2 muxer write zero frames and still exit
    0. A batch would then report N files compressed with the savings silently
    missing from the ratio."""
    run, _, told = harness
    vt = ("#!/bin/zsh\n"
          'if [ "$1" = "--help" ]; then exit 0; fi\n'
          "exit 0\n")                      # clean exit, no file written
    r = run("Small - 720p", _video(tmp_path), vt_body=vt)
    assert r.returncode == 1, f"reported success with no output file: {told()!r}"
    assert "Could not compress" in told()
    assert not (tmp_path / "clip_compressed.mp4").exists()


def test_a_stale_output_from_an_earlier_run_is_not_counted(harness, tmp_path):
    """Claim: deleting the temp indirection lets a previous run's output be
    measured as this one's. This is structurally impossible now rather than
    guarded, which is why it is asserted against a *failing* encode."""
    run, _, told = harness
    stale = tmp_path / "clip_compressed.mp4"
    stale.write_bytes(b"x" * 16)
    vt = ("#!/bin/zsh\n"
          'if [ "$1" = "--help" ]; then exit 0; fi\n'
          "exit 1\n")
    r = run("Small - 720p", _video(tmp_path), vt_body=vt)
    assert r.returncode == 1, "counted a stale file as a successful compress"
    assert "Could not compress" in told()
    assert stale.read_bytes() == b"x" * 16, "clobbered the previous output"


def test_missing_ffmpeg_fails_before_prompting(harness, tmp_path):
    """Claim: deleting the pre-flight defers a guaranteed failure to per-file
    error handling, where it surfaces N times with ffmpeg's wording instead of
    once with a remedy.

    This is the failure the image action could never have: `compress` is pure
    PIL, so it never needed anything on PATH."""
    run, argfile, told = harness
    r = run("Small - 720p", _video(tmp_path), ffmpeg_dir="/nonexistent")
    assert r.returncode == 1
    assert not argfile.exists(), "prompted and ran despite no ffmpeg"
    assert "ffmpeg" in told() and "brew install" in told(), told()


def test_empty_ffmpeg_dir_does_not_put_cwd_on_path(harness, tmp_path):
    """Claim: deleting the guard makes an unsubstituted/empty @FFMPEG_DIR@
    expand to a leading empty PATH component, which POSIX reads as the current
    directory -- so a file named `ffmpeg` in the folder being right-clicked
    would execute."""
    run, _, _ = harness
    body = SCRIPT.read_text()
    assert 'if [ -n "$FFMPEG_DIR" ]; then' in body, \
        "PATH is prepended unconditionally; an empty value means cwd"
    r = run("Small - 720p", _video(tmp_path), ffmpeg_dir="")
    assert r.returncode == 1, "ran with an empty ffmpeg directory"


@pytest.mark.parametrize("name", [
    "my video's \"best\" take.mp4",
    "weird[1]name.mp4",
    "spaces and $dollars.mp4",
    "-leading-dash.mp4",
])
def test_hostile_filenames_survive(harness, tmp_path, name):
    run, argfile, _ = harness
    r = run("Small - 720p", _video(tmp_path, name))
    assert r.returncode == 0, r.stderr
    assert name.rsplit(".", 1)[0] in argfile.read_text()


def test_no_files_selected_exits_quietly(harness):
    run, argfile, _ = harness
    r = run("Small - 720p")
    assert r.returncode == 0
    assert not argfile.exists(), "must not invoke the binary with no input"


def test_cancelled_dialog_exits_quietly(harness, tmp_path):
    run, argfile, _ = harness
    r = run("CANCELLED", _video(tmp_path))
    assert r.returncode == 0
    assert not argfile.exists()


def test_unrecognised_choice_does_nothing(harness, tmp_path):
    """A preset renamed in the AppleScript but not the case statement must fail
    closed rather than silently compressing with default settings."""
    run, argfile, _ = harness
    r = run("Some Preset That No Longer Exists", _video(tmp_path))
    assert r.returncode == 0
    assert not argfile.exists()


def test_two_selected_files_with_the_same_stem_do_not_collide(harness, tmp_path):
    """Claim: deleting the collision check silently destroys one of two outputs.

    Every re-encode preset forces `.mp4`, so `clip.mov` and `clip.mp4` -- an
    entirely ordinary pairing, e.g. a phone video and its export -- both resolve
    to `clip_compressed.mp4`. The second overwrites the first, `ok` still counts
    two, and the ratio charges both source sizes against one output."""
    run, _, _ = harness
    _video(tmp_path, "clip.mp4")
    _video(tmp_path, "clip.mov")
    r = run("Small - 720p", tmp_path / "clip.mp4", tmp_path / "clip.mov")
    assert r.returncode == 0, r.stderr
    produced = sorted(p.name for p in tmp_path.iterdir() if "_compressed" in p.name)
    assert len(produced) == 2, f"one output overwrote the other: {produced}"


def test_an_output_never_overwrites_another_selected_file(harness, tmp_path):
    """Claim: deleting the collision check destroys a file the user selected.

    Select `clip.mp4` together with an existing `clip_compressed.mp4` -- the
    obvious thing to do when re-compressing a folder -- and the first
    iteration's output lands on the second iteration's input. The pre-existing
    file is gone before it is ever read."""
    run, _, _ = harness
    _video(tmp_path, "clip.mp4")
    existing = tmp_path / "clip_compressed.mp4"
    existing.write_bytes(b"PRECIOUS" * 64)
    r = run("Small - 720p", tmp_path / "clip.mp4", existing)
    assert r.returncode == 0, r.stderr
    assert existing.read_bytes().startswith(b"PRECIOUS"), \
        "an output overwrote a file that was itself selected as an input"


def test_a_rerun_does_not_replace_the_previous_result(harness, tmp_path):
    """Claim: deleting this makes the video action contradict the image one.

    `compress._unique_path` never lets a *derived* output replace an existing
    file -- a second run writes `photo_compressed-2.webp` -- and the README
    states that as a property of the tool. Two right-clicks on the same video
    must behave the same way, and the counter has to start at 2 so the second
    result reads as "-2" in both places."""
    run, _, _ = harness
    src = _video(tmp_path)
    assert run("Small - 720p", src).returncode == 0
    first = (tmp_path / "clip_compressed.mp4").read_bytes()
    assert run("Small - 720p", src).returncode == 0

    assert (tmp_path / "clip_compressed.mp4").read_bytes() == first, \
        "second run replaced the first result"
    assert (tmp_path / "clip_compressed-2.mp4").exists(), \
        f"expected -2, got {sorted(p.name for p in tmp_path.iterdir())}"


def test_a_selection_with_no_regular_files_says_nothing(harness, tmp_path):
    """Claim: deleting this guard pops a *critical* alert reading "Could not
    compress 0 file(s)."

    Non-files are skipped before either counter moves, so ok=0 and failed=0 is
    reachable, and the failure branch fires on `ok -eq 0` alone."""
    run, argfile, told = harness
    (tmp_path / "adir").mkdir()
    r = run("Small - 720p", tmp_path / "adir", tmp_path / "gone.mp4")
    assert r.returncode == 0, f"alarmed the user over an empty selection: {told()!r}"
    assert not argfile.exists()
    assert "0 file(s)" not in told(), f"nonsense count shown: {told()!r}"


def test_directories_and_missing_files_are_skipped(harness, tmp_path):
    run, argfile, _ = harness
    (tmp_path / "adir").mkdir()
    r = run("Small - 720p", tmp_path / "adir", tmp_path / "nope.mp4",
            _video(tmp_path))
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in argfile.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one invocation, got {lines}"


def test_failure_alert_names_the_cause(harness, tmp_path):
    """Claim: deleting this lets the alert go back to a bare count, which is
    what a half-installed venv produced on the image action -- the real cause
    was on stderr and discarded."""
    run, _, told = harness
    vt = ("#!/bin/zsh\n"
          'if [ "$1" = "--help" ]; then exit 0; fi\n'
          'echo "Unknown encoder libx264" >&2\n'
          "exit 1\n")
    r = run("Small - 720p", _video(tmp_path), vt_body=vt)
    assert r.returncode == 1
    assert "Unknown encoder" in told(), f"cause not surfaced: {told()!r}"


def test_progress_bar_output_does_not_crowd_out_the_cause(harness, tmp_path):
    """Claim: deleting the \\r-splitting reduces the alert to progress bars.

    `vidtools pipeline` installs a tqdm sink that writes carriage-return-updated
    bar frames to stderr. Measured on a real run: 1313 bytes containing exactly
    *one* newline. So `tail -n 3` returns the entire bar and the real cause is
    pushed past what the dialog shows -- which is the 0.28.0 regression
    ("the alert named no cause") arriving by a different route.

    Video-only: the image action's `compress` never reports progress, which is
    why this could not happen there."""
    run, _, told = harness
    # Real CRs and literal %, as tqdm emits them: %% survives printf, \r is
    # turned into a carriage return by printf rather than by Python.
    bar = (r"\rProcessing:   0%%|          | 0/100 [00:00<?, ?%%/s]"
           r"\rProcessing:  50%%|#####     | 50/100 [00:01<00:01, 40.0%%/s]"
           r"\rProcessing: 100%%|##########| 100/100 [00:02<00:00, 41.2%%/s]\n")
    vt = ("#!/bin/zsh\n"
          'if [ "$1" = "--help" ]; then exit 0; fi\n'
          f'printf \'{bar}\' >&2\n'
          'echo "Error: Conversion failed: No space left on device" >&2\n'
          "exit 1\n")
    r = run("Small - 720p", _video(tmp_path), vt_body=vt)
    assert r.returncode == 1
    m = re.search(r'message "(.*)" as critical', told(), re.S)
    assert m, f"no alert recorded: {told()!r}"
    body = m.group(1)
    assert "No space left on device" in body, f"cause lost: {body!r}"
    assert "Processing:" not in body, f"alert is progress-bar noise: {body!r}"


def test_alert_message_survives_quotes_and_backslashes(harness, tmp_path):
    """Claim: deleting _as_quote turns an error containing " or \\ into an
    AppleScript syntax error, so the user sees *nothing* -- on the code path
    whose only job is reporting failure. Fed back through real osascript, using
    `return` so no modal is drawn."""
    run, _, told = harness
    nasty = 'cannot open "C:\\temp\\x" -- bad'
    vt = ("#!/bin/zsh\n"
          'if [ "$1" = "--help" ]; then exit 0; fi\n'
          f'print -r -- {nasty!r} >&2\n'
          "exit 1\n")
    r = run("Small - 720p", _video(tmp_path), vt_body=vt)
    assert r.returncode == 1
    m = re.search(r'message "(.*)" as critical', told(), re.S)
    assert m, f"no alert recorded: {told()!r}"
    probe = subprocess.run(["/usr/bin/osascript", "-e", f'return "{m.group(1)}"'],
                           capture_output=True, text=True)
    assert probe.returncode == 0, \
        f"alert body is not valid AppleScript, so no dialog appears: {probe.stderr}"


def test_binary_present_but_not_runnable_fails_before_prompting(harness, tmp_path):
    """Claim: deleting this lets `-x` stand in for "works". An editable install
    whose package went missing is executable and exits 1 on every call."""
    run, argfile, told = harness
    r = run("Small - 720p", _video(tmp_path),
            vt_body="#!/bin/zsh\necho 'ModuleNotFoundError' >&2\nexit 1\n")
    assert r.returncode == 1
    assert not argfile.exists()
    assert "install.sh" in told(), f"no actionable remedy offered: {told()!r}"


def test_size_report_is_present_and_right(harness, tmp_path):
    """The stub writes 30 bytes for an 8 KB input, so a correct report is a few
    hundred x. A wrong file measured yields either no ratio or a silly one."""
    run, _, told = harness
    r = run("Small - 720p", _video(tmp_path, size=8192))
    assert r.returncode == 0, r.stderr
    assert "x smaller" in told(), f"no ratio reported: {told()!r}"
    ratio = float(told().split("x smaller")[0].split(",")[-1].strip())
    assert ratio > 50, f"ratio {ratio} suggests the wrong file was measured"


def test_reencode_notifies_before_starting(harness, tmp_path):
    """Claim: deleting this leaves a multi-minute encode with no acknowledgement
    that the right-click did anything."""
    run, _, told = harness
    r = run("Small - 720p", _video(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "Compressing" in told()


def test_strip_only_does_not_double_notify(harness, tmp_path):
    """The stream-copy preset is effectively instant; a "starting" notification
    would land on top of its own completion."""
    run, _, told = harness
    r = run("Strip metadata only - no re-encode", _video(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "Compressing" not in told()
    assert "metadata removed" in told()


def test_script_is_zsh_not_bash():
    assert SCRIPT.read_text().startswith("#!/bin/zsh")


def test_script_keeps_its_placeholders():
    """Absolute paths committed here would be path leaks."""
    body = SCRIPT.read_text()
    assert "@VIDTOOLS_BIN@" in body
    assert "@FFMPEG_DIR@" in body


def test_embedded_applescript_compiles_cleanly(tmp_path):
    """Because the test harness stubs osascript to avoid drawing modal GUI
    windows, a syntax error in the embedded AppKit AppleScript block would pass
    every stubbed test and fail at right-click time. `/usr/bin/osacompile`
    verifies the AppleScript/ObjC syntax without executing `runModal()`."""
    text = SCRIPT.read_text()
    block = text.split("<<'APPLESCRIPT'\n", 1)[1].split("\nAPPLESCRIPT", 1)[0]
    scpt = tmp_path / "dialog.scpt"
    r = subprocess.run(
        ["/usr/bin/osacompile", "-o", str(scpt), "-"],
        input=block, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"AppleScript syntax error: {r.stderr}"
