"""The Quick Action shell script, driven end-to-end with stubs.

Two of the three Quick Action defects lived here, not in the plist, and neither
was reachable from Python: the `set -u` empty-array crash that killed the one
preset passing no flags, and a size report that measured a stale file from an
earlier run. Both needed the script to actually *run*.

`vidtools` and `osascript` are stubbed. The vidtools stub records argv and
writes a plausible output file, so preset-to-flag mapping and the size report
are exercised without invoking the real compressor.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not Path("/bin/zsh").exists(),
    reason="needs macOS zsh and BSD stat",
)

SCRIPT = Path(__file__).resolve().parents[1] / "macos" / "compress-image.sh"

PRESETS = {
    "Smallest - sized for AI vision (WebP)": ["--fit", "vision"],
    "Smallest - sized for AI vision (AVIF, not readable by AI tools)":
        ["--fit", "vision", "--allow-avif"],
    "Balanced (WebP)": [],                       # the empty-array case that crashed
    "Visually lossless (WebP)": ["--target-ssim", "0.995"],
    "Lossless - exact pixels": ["--lossless"],
    "Balanced, keep metadata": ["--preserve-metadata"],
}


@pytest.fixture
def harness(tmp_path):
    """Return (run, argfile). `run(choice, *files)` executes the script."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argfile = tmp_path / "args.txt"

    # vidtools stub: record argv, write <out>.jpg, print the `wrote` line the
    # script parses to locate the output.
    vt = bin_dir / "vidtools"
    vt.write_text(
        "#!/bin/zsh\n"
        f'print -r -- "$@" >> "{argfile}"\n'
        'out="${@[-1]}"\n'
        'printf "padding padding padding" > "$out.jpg"\n'
        'echo "wrote $out.jpg"\n'
    )
    vt.chmod(0o755)

    def run(choice, *files, extra_env=None):
        # The script redirects notify()/fail() output to /dev/null, so the stub
        # logs `-e` invocations to a file instead -- that is the only way to see
        # what the user was actually told.
        osa = bin_dir / "osascript"
        osa.write_text(
            "#!/bin/zsh\n"
            'if [ "$1" = "-e" ]; then\n'
            f'  print -r -- "$2" >> "{tmp_path}/notify.txt"\n'
            "  exit 0\n"
            "fi\n"
            f"print -r -- {choice!r}\n"
        )
        osa.chmod(0o755)
        body = SCRIPT.read_text().replace("@VIDTOOLS_BIN@", str(vt))
        body = body.replace("OSASCRIPT=/usr/bin/osascript", f"OSASCRIPT={osa}")
        runner = tmp_path / "run.sh"
        runner.write_text(body)
        runner.chmod(0o755)
        env = {**os.environ, **(extra_env or {})}
        # Execute by path so the script's own shebang selects the interpreter.
        # Forcing /bin/zsh here would mask a regression to /bin/bash, which is
        # 3.2.57 on macOS and dies on the no-flag preset -- the exact bug this
        # file exists to catch.
        return subprocess.run([str(runner), *map(str, files)],
                              capture_output=True, text=True, env=env)

    return run, argfile


def _image(tmp_path, name="pic.png", size=4096):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * size)
    return p


@pytest.mark.parametrize("choice,expected", PRESETS.items(), ids=lambda v: str(v)[:28])
def test_every_preset_maps_to_its_flags(harness, tmp_path, choice, expected):
    """Includes 'Balanced (WebP)', which passes no flags. Under /bin/bash 3.2
    that expanded an empty array with `set -u` and aborted the whole script
    before compressing anything -- the reason this file exists."""
    run, argfile = harness
    img = _image(tmp_path)
    r = run(choice, img)
    assert r.returncode == 0, r.stderr
    recorded = argfile.read_text().split()
    assert recorded[0] == "compress"
    assert recorded[1:1 + len(expected)] == expected


def test_balanced_preset_actually_compresses(harness, tmp_path):
    """The regression proper: the crashing preset must reach the binary."""
    run, argfile = harness
    r = run("Balanced (WebP)", _image(tmp_path))
    assert r.returncode == 0, r.stderr
    assert argfile.exists() and "compress" in argfile.read_text()


@pytest.mark.parametrize("name", [
    "my photo's \"best\" shot.png",
    "weird[1]name.png",
    "spaces and $dollars.png",
    "-leading-dash.png",
])
def test_hostile_filenames_survive(harness, tmp_path, name):
    run, argfile = harness
    r = run("Balanced (WebP)", _image(tmp_path, name))
    assert r.returncode == 0, r.stderr
    assert name.rsplit(".", 1)[0] in argfile.read_text()


def test_stale_output_from_an_earlier_run_is_not_measured(harness, tmp_path):
    """A previous run that chose a different format leaves
    <stem>_compressed.<other-ext> behind. Globbing for the output could measure
    that instead and report a saving against the wrong file."""
    run, _ = harness
    img = _image(tmp_path, size=8192)
    (tmp_path / "pic_compressed.avif").write_bytes(b"")  # 0 bytes, stale
    r = run("Balanced (WebP)", img)
    assert r.returncode == 0, r.stderr

    # The stub writes a 23-byte output for an 8 KB input, so a correct report is
    # a few hundred x. Measuring the 0-byte stale .avif instead yields total_after
    # of 0, which suppresses the ratio entirely -- so assert the ratio is
    # *present and right*, not merely that nothing looked malformed.
    told = (tmp_path / "notify.txt").read_text()
    assert "x smaller" in told, (
        f"no ratio reported -- the stale 0-byte file was measured: {told!r}")
    ratio = float(told.split("x smaller")[0].split(",")[-1].strip())
    assert ratio > 50, f"ratio {ratio} suggests the wrong file was measured"


def test_no_files_selected_exits_quietly(harness):
    run, argfile = harness
    r = run("Balanced (WebP)")
    assert r.returncode == 0
    assert not argfile.exists(), "must not invoke the binary with no input"


def test_cancelled_dialog_exits_quietly(harness, tmp_path):
    run, argfile = harness
    r = run("CANCELLED", _image(tmp_path))
    assert r.returncode == 0
    assert not argfile.exists()


def test_unrecognised_choice_does_nothing(harness, tmp_path):
    """A preset label renamed in the AppleScript but not the case statement must
    fail closed rather than silently compress with default flags."""
    run, argfile = harness
    r = run("Some Preset That No Longer Exists", _image(tmp_path))
    assert r.returncode == 0
    assert not argfile.exists()


def test_directories_and_missing_files_are_skipped(harness, tmp_path):
    run, argfile = harness
    (tmp_path / "adir").mkdir()
    r = run("Balanced (WebP)", tmp_path / "adir", tmp_path / "nope.png",
            _image(tmp_path))
    assert r.returncode == 0, r.stderr
    # Count invocations by line -- "compress" also appears inside the output
    # path (<stem>_compressed), which made a substring count read 2.
    lines = [ln for ln in argfile.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one invocation, got {lines}"


def test_missing_binary_fails_loudly(harness, tmp_path):
    """The action is welded to an absolute venv path. If the repo moves it must
    alert, not silently do nothing."""
    run, _ = harness
    body = SCRIPT.read_text().replace("@VIDTOOLS_BIN@", str(tmp_path / "gone"))
    osa = tmp_path / "osa"
    osa.write_text("#!/bin/zsh\nprint -r -- 'Balanced (WebP)'\n")
    osa.chmod(0o755)
    body = body.replace("OSASCRIPT=/usr/bin/osascript", f"OSASCRIPT={osa}")
    runner = tmp_path / "r2.sh"
    runner.write_text(body)
    r = subprocess.run(["/bin/zsh", str(runner), str(_image(tmp_path))],
                       capture_output=True, text=True)
    assert r.returncode == 1


def test_script_is_zsh_not_bash():
    assert SCRIPT.read_text().startswith("#!/bin/zsh")


def test_script_keeps_the_placeholder():
    """An absolute binary path committed here would be a path leak."""
    assert "@VIDTOOLS_BIN@" in SCRIPT.read_text()
