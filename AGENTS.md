# AGENTS.md

Guidance for coding agents (Claude Code, Antigravity, etc.) working in this
repository. `CLAUDE.md` delegates here via `@AGENTS.md`. This file holds the
rules and traps; the incidents behind them are in `docs/history.md`.

## Project

vidtools is an FFmpeg + PIL + yt-dlp media processing CLI (`vidtools` / `vt` console scripts).

There are two surfaces: the CLI, and the macOS Finder Quick Actions (image and
video) under `macos/`. The web UI was removed in 0.16.0. Each action embeds a
`vidtools` invocation into its bundle at install time, so a CLI change does not
reach it until `macos/install.sh` is re-run.

Five files under `macos/`: `install.sh` (orchestrates), `make_workflow.py`
(builds both plists), `compress-image.sh` / `compress-video.sh` (the embedded
action bodies), and `enable_service.py` (the enable record — see *Registration
is two things* below; without it a perfectly built bundle is invisible).

**Two bundles, one installer.** `Compress Image` registers for `public.image`
and calls `compress`; `Compress Video` registers for `public.movie` and calls
`compress --recipe <recipe>`. One bundle accepting both types would appear on
every file and would have to guess at a mixed selection, which has no correct
preset list. `make_workflow.py` is parameterised by `kind` (`image`/`video`);
both identifier pairs were read off a Quick Action macOS itself ships
(`/System/Library/Automator/Encode Media.action/.../Encode Selected Video
Files.workflow`), not inferred — that bundle is the reference to diff against
when anything about registration looks wrong. Both Quick Actions use an AppKit
`NSAlert` with an `NSPopUpButton` for preset selection and a suppression
checkbox (`Keep source metadata`) so every preset strips metadata by default and
can be overridden in the same dialog (`<preset>|KEEP_META`).

**The video action needs `@FFMPEG_DIR@` baked in; the image action does not.**
Automator's PATH holds neither `/usr/local/bin` nor Homebrew's prefix. Image
`compress` is pure PIL; every video preset shells out to ffmpeg on every file.
`install.sh` resolves the directory of the `ffmpeg` symlink (not its target — a
Homebrew bin entry survives version bumps, the Cellar path it points into does
not), preferring `<prefix>/opt/ffmpeg-full/bin` when Homebrew's keg-only
`ffmpeg-full` is present, and passes it to the generator. With no ffmpeg found
the video action is **skipped with a warning** and the image action still
installs.

Python >=3.14. Requires `ffmpeg` >= 5.1 and `yt-dlp` in PATH.

**Homebrew's `ffmpeg` formula is slim** — no libass, libwebp, libaom-av1 or
libvorbis. `ffmpeg-full` is the batteries-included one, but it is **keg-only**:
`brew install ffmpeg-full` alone changes nothing, because Homebrew does not link
it. It needs `/opt/homebrew/opt/ffmpeg-full/bin` on PATH ahead of whatever else
provides `ffmpeg`, or the capability probe keeps reporting the old build's gaps.

Worth checking `file "$(which ffmpeg)"` before believing any encoder result: an
x86_64 build under Rosetta on Apple silicon runs fine but **cannot open a
VideoToolbox compression session**, so `--hwaccel` degrades to nothing with no
error that names the cause.

Consequences in this codebase, all verified against a slim build:

- `subtitles` burn-in needs libass and is unavailable; `add_subtitles` checks for
  the filter and says so rather than emitting a filter-parse error naming no cause.
- `--video-codec libaom-av1` needs libaom-av1, and `.ogg` output needs libvorbis.
  Both are capability-checked via `utils.require_encoder`, which names the gap
  and points at `ffmpeg-full`.
- libwebp is already routed around: `screenshot` and `compress` encode WebP
  through PIL, so they work regardless.

Every filter the tool uses (scale, transpose, crop, noise, afade, loudnorm,
afftdn, acompressor, highpass, aresample) is present in the slim build. Only
encoders and libass are affected.

## Conventions (READ FIRST)

- **uv only**. Never `pip`/`python -m pip`/bare `python`. Run scripts with `uv run`, install with `uv add`, sync with `uv sync`.
- **orjson everywhere**, never stdlib `json`. File I/O: `open(..., "rb")` / `open(..., "wb")` since orjson works with bytes.
- **No `progress_callback` parameter on operations.** Use the thread-local sink: `from .progress import report_progress; report_progress(percent, msg)`. Callers install sinks via `progress_context(sink)`.
- **No `exit(1)` in library code, and no raw subprocess exceptions.** Raise `FfmpegError` (from `vidtools.progress`) or `ValueError`; `main()` catches only those two, so anything else reaches the user as a traceback printing absolute paths.
- **Probe with `utils.probe_json`**, never `subprocess.run(["ffprobe", ...], check=True)` — it translates a failed or missing ffprobe into `FfmpegError`.
- **Never call bare `Image.open` on a user file, in any module.** Use `image_ops._open_image` (renders pixels: applies EXIF orientation, keeps ICC) or `_open_raw` (inspect-only). In-memory encode/decode of a `BytesIO` inside `compress.py` is not a file open and is fine.
- **No stub handlers.** Only register CLI commands when the operation actually exists. Conversely, do not keep exported ops that no command reaches.
- **Reuse helpers in `video_ops.py`**: `_add_faststart_if_mp4`, `_add_time_range_args`, `_apply_encode_options`, `_build_scale_filter`, `_build_transpose_filter`, `_escape_subtitle_path`, `_get_video_encoder`, `_preferred_aac_encoder`, `_add_deep_strip_args`, `_TRANSPOSE_MAP`, `DEFAULT_VIDEO_CODEC`, `DEFAULT_AUDIO_CODEC`. Pipeline.py imports them — don't duplicate. Use `_apply_encode_options` for the standard `-c:v / -c:a / -crf|-q:v` block (handles `crf=0` correctly; raw `if crf:` does not).
- **`_build_scale_filter` has four modes, and `max_edge` is the only one that can decline to act.** `-p`/`-W`/`-H` are unconditional targets; `--max-edge N` is a *cap* that never enlarges and fits inside a square box so it caps the longest edge whichever way the video is turned. Both properties are load-bearing for the Finder presets, which are applied to whatever was selected: without the `min()` on each axis, `force_original_aspect_ratio=decrease` scales a small input *up* to fill the box; without the square box, "720p" cuts portrait phone video to 406x720 while leaving landscape at 1280x720. `force_divisible_by=2` is not decoration either — 1918x1080 capped at 1280 derives an odd height, and yuv420p cannot store one, so the encode fails outright.
- All ffmpeg commands include `-y` to avoid interactive stalls.

## Architecture

Modules under `vidtools/`:

- **operations.py** — thin facade (`from .video_ops import *` etc.). Existing imports (`from vidtools.operations import cut_video`) still work. Add new ops to the appropriate `*_ops.py` file, not here.
- **video_ops.py** — FFmpeg video operations + `compress_video` (named recipes in `VIDEO_COMPRESS_RECIPES` with smart `<100 MB` budget handling, 10-bit HEVC, VideoToolbox, AudioToolbox `aac_at`, and deep metadata/SEI stripping) + shared filter-builder helpers reused by `pipeline.py`.
- **image_ops.py** — PIL image operations (no ffmpeg). Synchronous; no progress reporting needed. **Every op that renders pixels routes through `_open_image` / `_save_image`** — do not call `Image.open` / `.save` directly. `_open_image` bakes EXIF orientation into the pixels (so geometry ops act on what the user actually sees) and carries the ICC profile across; `_save_image` flattens alpha for containers that can't store it, converts to a mode the container declares in `_FORMAT_MODES`, passes `quality` to every encoder that takes one, re-attaches the ICC profile, and applies the metadata policy via `_build_exif`. `strip_image_metadata` is the deliberate exception: it edits the container rather than re-rendering, so it has its own save path.

  Three call sites legitimately need the *undecoded* original — `strip_image_metadata` plus the two inspectors (`get_image_info`, `describe_color_profile`), which must not transpose what they report on. They open via **`_open_raw`**, which shares `_open_image`'s hostile-input error translation without applying orientation or ICC.
- **compress.py** — measured format *and* quality search for images. Encodes candidates with Pillow (no `cwebp`/`avifenc`/`oxipng` subprocesses), scores each against the original with SSIM, and writes the smallest that meets the fidelity target. Full-image SSIM is authoritative; the high-variance probe crop only narrows the starting bracket and can never veto a candidate. Defaults to PNG/JPEG/WebP — AVIF compresses roughly 2x better but no major vision API decodes it, so it's opt-in behind `--allow-avif` (naming a `.avif` output implies the flag). A content classifier (flat-run fraction + hard-edge density) raises the target to `FLAT_TARGET_SSIM` for text/line art, whose ringing SSIM barely registers. SSIM is a mediocre perceptual metric, chosen because it's computable with numpy alone and is monotonic in encode quality. Opens via `_open_image` and applies `image_ops._build_exif` so `--preserve-metadata` works on candidate encodes too.

  **`_unique_path` sets a project-wide convention, not a local one:** a
  *derived* output never replaces an existing file, and the counter starts at 2
  so the second result reads `<stem>-2<ext>`. `macos/compress-video.sh` and
  `video_ops.compress_video` implement the same rule and must keep the same
  spelling. An output the caller *names* is still overwritten; auto-renaming
  what was asked for would break scripts.
- **audio_ops.py** — FFmpeg audio operations including two-pass loudnorm in `normalize_audio` and the single-pass vocal cleanup chain in `clean_audio`.
- **separation.py** — Audio source separation behind a `SeparationBackend` Protocol. Current backend (`AudioSeparatorBackend`) wraps `audio-separator`. Optional extra: `uv sync --extra separation`. Caches the loaded model by `(model_name, output_dir)` so back-to-back calls don't reload weights.
- **utils.py** — `run_ffmpeg(command)` (always raises `FfmpegError`, reports progress via the thread-local sink, coalesces per-frame events to per-percent), `probe_json(path, *extra_args)` for ffprobe, `check_ffmpeg_installed()` (>= 5.1), `is_valid_file()`, and the ffmpeg **capability probe** (`_ffmpeg_capabilities`, `_ffmpeg_has_filter`, `_ffmpeg_has_encoder`, `require_encoder`). One probe for filters and encoders both; a *failed* probe returns `None` and is deliberately not cached, since caching a transient timeout would report a capability as missing until restart. `None` must never be treated as "absent" — that reports a missing binary as a missing library.
- **progress.py** — Thread-local progress sink (~50 lines). `set_progress_sink`, `get_progress_sink`, `report_progress`, `progress_context` (CM with guaranteed cleanup), `FfmpegError`. Same pattern as Python `logging` / Flask `g`.
- **pipeline.py** — `Pipeline` and `PipelineStep`. Chains video ops into a single ffmpeg invocation with combined `-vf` filter chain. Validates: at most one of each step, no `--copy` (filters require re-encode), `sanitize` requires `--manual-crop`. Cut input args pulled to before `-i`; convert always terminal.
- **cli.py** — argparse setup with handlers defined inline (no separate main.py). `_make_smart` factory + `_detect_kind` for input-type dispatch. `_parse_pipeline_steps` walks argv splitting on op keywords. **Flags shared by a verb and its pipeline step are declared once** (`_add_resize_flags`, `_add_crop_flags`) and used by both parsers; a hand-written copy in `_make_step_parser` drifts.
- **ytdlp.py** — yt-dlp subprocess wrapper. Reports progress via thread-local sink. Uses `--print after_move:filepath` for reliable output filename detection.
  - `download()` — standard URL download (mp3/wav/mp4) with percent-based progress.
  - `download_live()` — live-broadcast capture (mp4-only). Polls for the show to start (`--wait-for-video`), records from second 0 (`--live-from-start`), survives network blips (`--retries infinite --fragment-retries infinite`). Reports fragment-based progress (throttled to every 5th fragment so multi-hour streams don't emit thousands of progress events).
  - Shared `_run_ytdlp_process(cmd, on_line)` helper handles subprocess lifecycle including graceful Ctrl-C (SIGTERM → yt-dlp finalizes/remuxes the partial file).

## Key patterns

1. **Command Flow (CLI)**: argparse → inline handler closure in cli.py → operation in `*_ops.py` → `run_ffmpeg(cmd)`. CLI installs a lazy tqdm sink via `progress_context` in `handle_command`.
2. **Progress Reporting**: operations call `report_progress(percent, msg)`. Sink is `threading.local()`, isolated per worker thread. No-op if no sink installed.
3. **Error Handling**: operations raise `FfmpegError` / `ValueError`. CLI catches in `main()` → exit 1.
4. **Unified verbs + Smart Dispatch**: one verb per concept, no `img-`/`audio-` prefixes (removed in 0.12.0). Multi-media verbs auto-detect input kind via `_detect_kind(path)` (`.gif` is video) and route through `_make_smart`: `info`, `convert`, `resize`, `crop`, `trim` (alias `cut`), `rotate`, `compress`. Single-media verbs are top-level too: audio = `normalize`/`fade`/`resample`/`clean`/`separate`; image = `thumbnail`/`strip-metadata`/`optimize`/`adjust`; video = `screenshot`/`extract-audio`/`extract-frames`/`concat`/`subtitles`/`sanitize`/`slideshow`/`add-audio`/`match-duration`/`srt-from-transcript`/`dl-live`. `convert` takes a unified `--quality` (1-100) mapped per media (`_quality_to_crf`/`_quality_to_audio_bitrate` in cli.py), with `--crf`/`--bitrate` overrides. The audio-processing verbs (`normalize`/`fade`/`resample`/`clean`) accept video inputs. The routing lives in `video_ops.apply_audio_op_keep_video(in, out, audio_op)` (used by the CLI `_audio_verb` wrapper): audio output runs the op directly; video-in + video-out processes the audio to a temp **`.m4a` (AAC)** and remuxes it over the original video (`add_audio` replace, video copied). The `.m4a`/AAC temp matters — a raw-PCM `.wav` muxes into mp4 as a non-standard stream. `VIDEO_EXTS`/`AUDIO_EXTS`/`IMAGE_EXTS` live in `utils.py` (single source of truth).
5. **Pipelines**: `vidtools pipeline in.mp4 out.mp4 trim --start 60 --end 90 resize -p 0.5 convert --crf 20`. Single ffmpeg invocation. Video only. `trim` and `cut` both map to the internal `cut` step.
6. **Metadata privacy (default on)**: identifying metadata is stripped from output unless the caller opts back in. See the section below — this is the single most surprising thing about the tool's behaviour, and any new op has to make a decision about it.

## Metadata privacy policy

**Identifying metadata is stripped by default.** GPS coordinates, camera
make/model and capture timestamps do not survive into output. `--preserve-metadata`
opts back in. Rendering-critical data — EXIF orientation, ICC colour profile —
is always preserved, because it is part of the image rather than an annotation
on it.

The rationale: these verbs write a *new* file, so the original keeps everything,
while a leaked location does not come back. The dominant use here is
compress-to-share. This deliberately differs from macOS's Convert Image, which
preserves by default.

Granularity differs between media by necessity:

- **Images** (`image_ops._build_exif`) use an *allowlist* — `_PROVENANCE_TAGS`
  (ImageDescription, Artist, Copyright, NewSubfileType). So `--preserve-metadata`
  restores authorship without re-admitting GPS, and a tag nobody has classified
  fails closed. JPEG output also clears the `comment` COM marker explicitly:
  Pillow reads it from `im.info` when the caller supplies none, which bypasses
  the allowlist entirely.
- **Video** (`video_ops._add_metadata_policy`) emits `-map_metadata -1`,
  `-map_metadata:s -1`, `-map_chapters -1`, `-fflags +bitexact`,
  `-flags:v +bitexact`, and `-flags:a +bitexact`. `compress_video` additionally
  blanks stream `encoder` tags and strips H.264/HEVC encoder SEI user data
  (`-bsf:v filter_units=remove_types=6` for `libx264`, `-x265-params no-info=1`
  for `libx265`). ffmpeg's `-map_metadata` is all-or-nothing at the container
  level, so for video `--preserve-metadata` keeps *everything*, location
  included.

Rotation is unaffected: on a stream copy the display matrix survives
`-map_metadata -1` intact; on a re-encode ffmpeg auto-rotates during decode, so
the pixels come out upright and the matrix is no longer needed.

**Audio inverts the default, deliberately.** For a photo or clip, metadata is
incidental. For audio it is frequently the point -- artist, title and album are
what make a library work, and `convert song.mp3 song.m4a` silently returning an
untitled file would be a worse bug than the leak. So the same three-category
model applies with different membership: descriptive tags (artist, title, album,
track, genre, date) are **kept**, while free-form and recorder fields
(`comment`, `description`, `encoded_by`, `location`, `make`, `model`) are
dropped. `comment` matters most -- it is the field that held 109 KB of
generation prompt in the video case. Implemented by keeping the source map and
blanking specific keys, avoiding a separate ffprobe pass.

**Verbs exposing `--preserve-metadata`**: `convert`, `resize`, `crop`, `trim`,
`rotate`, `compress`, `concat`, `add-audio`, `match-duration`, `thumbnail`,
`optimize`, `adjust`, `pipeline`, `subtitles`, `normalize`, `fade`, `resample`,
`clean`, `extract-audio`, `extract-frames`, `slideshow`.
Added to a parser via `cli._add_preserve_metadata`.

**`README.md` restates this policy and duplicates the verb list.** Update both, or it silently drifts.

`extract-audio` takes the **audio** policy rather than the video one — its
output is an audio file, so title/artist survive while location and comment are
blanked. `extract-frames` and `slideshow` strip.

**Known gaps — do not claim coverage the code does not have:**

- `sanitize` always strips and has no opt-in (by design — that is the verb's job).

## Common tasks

### Run the CLI

```bash
uv run vidtools <command> [options]

# Universal verbs auto-detect type from input extension (no img-/audio- prefixes)
vidtools info file.{mp4,png,mp3}
vidtools resize file.mp4 out.mp4 -p 0.5
vidtools resize file.png out.png -p 0.5
vidtools compress photo.jpg
vidtools compress clip.mov --recipe universal
vidtools convert in.wav out.mp3                      # audio convert
vidtools trim --start 60 --end 90 song.mp3 clip.mp3  # alias: cut

# Single-media verbs are also top-level (no prefix)
vidtools normalize in.mp3 out.mp3 --target -16
vidtools thumbnail -s 256 in.png thumb.png

# Pipelines
vidtools pipeline in.mp4 out.mp4 trim --start 60 --end 90 resize -p 0.5 convert --crf 20
```

### Run tests

```bash
uv run pytest tests/ -v
```

Two real-toolchain tiers, separated by marker rather than by file:

- `@pytest.mark.ffmpeg` — needs real `ffmpeg`/`ffprobe` on PATH. `conftest.pytest_collection_modifyitems` skips these, and only these, when the binaries are absent.
- `@pytest.mark.pil_real` — exercises real PIL encode/decode. No external toolchain, so **these must never be gated behind ffmpeg**; that is the whole reason skipping is marker-driven. `test_imaging_contract.py` and `test_cli_image_paths.py` live in this tier.

Both markers are registered in `pyproject.toml` under `--strict-markers`, so a typo'd marker fails collection rather than silently running everywhere. Everything else mocks `run_ffmpeg`.

### Quick Action tests

`test_macos_quick_action.py` (image) and `test_macos_video_action.py` (video)
run the Quick Action *shell scripts* under their own shebang with stubbed
`vidtools`/`osascript`/`ffmpeg`. Execute by path rather than forcing `/bin/zsh`
— forcing the interpreter masks a regression to `/bin/bash`, which is 3.2.57 on
macOS and dies on a preset that passes no flags. macOS-only; skipped elsewhere.
They stay separate files: the scripts are separate installed artifacts with
different contracts.

**Two traps specific to driving zsh scripts from pytest:**

- **`status` is read-only in zsh** (it is the spelled-out form of `$?`).
  Assigning to it aborts the script outright. The video script uses `rc`.
- **zsh sources its per-user env file for every invocation, scripts included.**
  A dotfile that composes PATH there restores a full PATH underneath any test
  that restricts it. The harness points `ZDOTDIR` at an empty directory.

`test_macos_workflow.py` asserts on the dicts `make_workflow.build_workflow` /
`build_info_plist` return, plus its `main()` guards driven as a subprocess.
`test_macos_install.py` runs `install.sh` for real against a temp Services dir
(`VIDTOOLS_SERVICES_DIR` overrides the install location — nothing but tests
should set it) and checks bundle *identity* across a reinstall. It builds a
throwaway repo copy (`fake_repo`) for the failure cases, and pins that the two
bundles register for *different* file types and fail independently.

`make_workflow.main()` **refuses to generate when `@VIDTOOLS_BIN@` is absent or
a placeholder has no value**. `str.replace` on a missing token is a silent no-op
that ships a bundle carrying no binary path; the installer-side assertion cannot
catch a *renamed* placeholder. It reads the script as explicit utf-8 because the
AppleScript block uses `¬` continuations.

**Mutation-test any new claim about these scripts.** Write the defect back in
and confirm a test goes red. A shell script driven through stubs has more ways
to pass vacuously than Python does — the stub can go inert and the assertions
still hold.

**Two shell hazards, both silent:**

- **`set -e` acts on the last command of an AND-list.** `[ -n "$X" ] && rm "$X"`
  puts the `rm` where `set -e` acts, so a failing rm aborts the enclosing
  function — fatal inside an EXIT trap. A conditional whose body can fail needs
  `if ...; then ...; fi`, not `&&`.
- **tqdm defeats line-based tailing of vidtools stderr.** The progress sink
  redraws with carriage returns, so a failing `compress` or `pipeline` run leaves
  stderr with a *single* newline and `tail -n 3` returns the progress bar instead
  of the error. Anything parsing vidtools stderr needs `tr '\r' '\n'` first and
  to drop frames matching `%|`.

Reaching a trap's recovery path from the outside can be impossible to stage.
Lifting the function text out of the script and driving it directly is a
legitimate answer — it is still the shipped code, called as a unit.

**`-x` is not an installability check.** It proves a file is executable, not
that it imports. `install.sh` and the embedded scripts run `"$BIN" --help` as a
pre-flight. The probe is `--help` because `vidtools` has **no `--version`
flag**. The video script also checks `command -v ffmpeg`, since the baked
directory goes stale if ffmpeg moves.

Alert text in the Quick Actions carries stderr and filenames, so it goes through
`_as_quote`: an unescaped `"` or `\` in an AppleScript string literal is a
*syntax error*, and the dialog never appears. `tests/test_macos_quick_action.py`
feeds the message back through real `osascript` using `return` rather than
`display alert`, so the test draws no modal.

### Verify the Quick Actions actually registered

```bash
SERVICES=$HOME/Library/Services   # path-privacy: ignore -- the OS decides where Quick Actions live

for n in 'Compress Image' 'Compress Video'; do
  /System/Library/CoreServices/pbs -dump_pboard | grep "$n"   # in the services DB
  plutil -lint "$SERVICES/$n.workflow"/Contents/*             # both plists parse
done

# Diff against one Automator wrote. macOS ships the exact video analogue:
plutil -p '/System/Library/Automator/Encode Media.action/Contents/Resources/Encode Selected Video Files.workflow/Contents/Resources/document.wflow'
```

`install.sh` printing "installed:" only means files were written. Check the OS's
view, not the layer just edited — this feature has failed in seven distinct
layers (see `docs/history.md`).

**Registration is two things, not one, and the second is invisible.** A bundle
can be complete, lint-clean, and listed by `pbs -dump_pboard` and still appear
nowhere — not in the Finder menu, and *not in System Settings → Login Items &
Extensions → Finder*. What is missing is an entry under `NSServicesStatus` in
the `pbs` preference domain, keyed `(null) - <menu name> - runWorkflowAsService`,
which is what the System Settings toggle writes. `macos/enable_service.py`
writes it at install time (add-only — an existing entry is the user's
preference, possibly a deliberate "off"), and Finder still needs `killall
Finder` to reload its menu.

**`defaults` lies twice, and both lies are destructive here.** `defaults import`
exits 0 whether or not it wrote anything, so `enable_service.py` reads the
domain back and confirms the key landed. And `defaults import` merges at the
*top* level but **replaces** nested keys, so any write of `NSServicesStatus`
must be built from the full current contents; `defaults export` emits a complete
`<dict/>` plist even for a domain that never existed, so an empty read means
failure, not "no entries", and writing on it would erase every other Quick
Action's toggle.

**Anything running `install.sh` from a test must set `VIDTOOLS_PBS_DOMAIN`.**
It falls back to the real `pbs` domain. `tests/test_macos_install.py` routes
every install through one `_test_env`, and
`test_test_installs_never_touch_the_real_preference_domain` fails if any
environment is built without it.

Red herrings, both confirmed meaningless by control:

- `lsregister -f` on a `.workflow` reports `-10811`. It does that on bundles
  that work too.
- `pbs -dump_pboard` reads 0 for a few seconds after `killall pbs` or `pbs
  -flush`, then recovers. Do not conclude a deregistration from it.

When a Quick Action misbehaves, **diff against a bundle on this machine that
works** — the user's own, and Apple's under `/System/Library/Services`.

The strongest end-to-end check is to pull `COMMAND_STRING` out of the
*installed* `document.wflow` and run that (not the repo copy) against a real
file under `env -i PATH=/usr/bin:/bin ZDOTDIR=<empty dir>`, with `osascript`
stubbed to return a preset. A run from a normal shell has ffmpeg on PATH and
proves nothing about the baked `@VIDTOOLS_BIN@` / `@FFMPEG_DIR@` paths.

### `install.sh` rules

- **Build into staging, then swap a fresh directory in.** Never delete the
  installed bundle before the replacement has built, and never rewrite files in
  place: an unchanged bundle-directory mtime keeps macOS's cached registration,
  and the action drops out of System Settings while still working from the
  right-click menu.
- **No recursive deletion in `macos/`.** Removal is `rm -f` on the two generated
  files plus `rmdir`, which refuses a non-empty directory and so cannot cascade.
  `remove_generated_bundle()` also refuses any path outside `$SERVICES`.
  `tests/test_macos_install.py::test_the_installer_never_deletes_recursively`
  keeps `rm -rf` out. The `rm -f` calls in the action scripts are single named
  temp files and are fine.
- **`remove_generated_bundle()` returns non-zero if anything was left behind.**
  Distinguish "not there" (success) from "there but not ours" (refuse). It takes
  `${1:-}`, not `$1` — an unbound positional under `set -u` aborts the caller,
  and one caller is the EXIT trap. It hardcodes the two filenames
  `make_workflow.py` writes; adding a third turns four installer tests red.
- **Run the venv's own `python3`, not PATH's.** The venv is a hard precondition
  by that point, so its interpreter is the one guaranteed to exist.

### Generate a test video for manual verification

```bash
ffmpeg -f lavfi -i testsrc=duration=4:size=640x480:rate=30 -y /tmp/test.mp4
```

## Adding a new operation

1. Implement in `video_ops.py`, `image_ops.py`, or `audio_ops.py`. **No `progress_callback` parameter.** Call `run_ffmpeg(command)` for ffmpeg, or `report_progress()` for explicit non-ffmpeg progress.
2. Raise `FfmpegError`/`ValueError` on failure. Never `exit(1)`.
3. For ffmpeg commands: include `-y`. Reuse helpers from `video_ops.py` for filter construction.
   - **Image input goes through `_open_image` / `_save_image`**, whichever module the op lives in. Never `Image.open` / `.save` directly — orientation, ICC profile, alpha flattening, container mode limits and the metadata policy all live there.
   - **Decide the metadata question explicitly.** Take a `preserve_metadata: bool = False` parameter, apply `video_ops._add_metadata_policy(command, preserve_metadata)` for ffmpeg ops (`_save_image` handles it for images), and expose the flag with `cli._add_preserve_metadata(parser)`. An op that carries source tags through by default is a leak, not a default.
4. Add CLI command + inline handler closure in `cli.py:setup_argparse()`.
5. Export from `vidtools/__init__.py`.
6. If it's a chainable video filter: wire into `_FILTER_BUILDERS` (pipeline.py) + `_PIPELINE_OP_NAMES` + `_make_step_parser` (cli.py). Declare its flags in one `_add_<op>_flags` helper used by both the verb and the step.
7. Write tests. **Mock at `vidtools.<ops_module>.run_ffmpeg`** — patching `vidtools.operations.run_ffmpeg` doesn't work because operations.py is a re-export facade. **Also add a real-ffmpeg test in `tests/test_integration.py` if the op is behavior-sensitive** (stream/container/codec choices, seeking, frame-at-timestamp, encoder availability). Mocked tests verify command *shape*; they cannot catch ffmpeg *behavior*. Rule of thumb: if you'd have to run ffmpeg by hand to be sure it works, the unit test won't catch it.

## Testing gotchas

- **Confirm a new test fails for the right reason.** Reintroduce the exact defect and check it goes red. Assertions a no-op also satisfies, mocks of output shapes ffmpeg never emits, and `pytest.raises(Exception)` have all produced tests that passed without testing anything.
- **Review subagents may lack a shell.** Findings reasoned from docs rather than execution are leads, not results — re-run them locally before acting.
- `monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", mock)` — NOT `vidtools.operations.run_ffmpeg`.
- Thread-local progress sink could leak between tests. The `progress_context()` CM auto-clears, and `conftest._isolate_progress_sink` is an autouse suite-wide safety net.
- Image op tests can use real in-memory PIL images (cheap, no ffmpeg required). Use the shared `conftest.py` fixtures rather than rolling your own: `quadrant_array`/`sample_image` (four distinct quadrants — a flat fill makes flip/rotate correctness untestable in principle, since a real flip and a no-op are pixel-identical on it), `palette_png`, `oriented_jpeg`, `private_jpeg` (carries GPS, for the metadata policy), `wide_gamut_png`/`icc_bytes`, `photo_png`, and `capture_ffmpeg` for command-shape assertions.
- Audio/video op tests mock `run_ffmpeg` (fast, command-shape assertions). For behavior that depends on what ffmpeg actually does, add a real-ffmpeg case in `tests/test_integration.py` instead. Mocks for shape, integration for behavior.

## Live recording (dl-live)

The `dl-live` CLI command records YouTube/HLS live broadcasts via `vidtools.ytdlp.download_live()`. Designed for shows that only stream live.

**Output is mp4-only.** Two reasons we don't expose `.ts`:

1. Passing `--hls-use-mpegts` OR `-o foo.ts` makes yt-dlp 2026.x invoke ffmpeg with the entire signed HLS master playlist URL as a single `-i` argument. YouTube's signed URLs run ~2 KB and overflow macOS `ARG_MAX` (256 KB), aborting with `OSError: Errno 63 File name too long`. Both triggers must be avoided.
2. yt-dlp's default HLS path writes MPEG-TS data into a `.mp4` file during recording (FixupM3u8 converts to proper mp4 at clean exit), so the partial-file-is-playable property is preserved without forcing `.ts`.

**Known issue (open).** Recording an HLS stream through `download_live()` aborts with `OSError: Errno 63 File name too long`. The shell form that works is *not* the same command: the library adds `--print after_move:filepath` and passes a literal `-o show.mp4` where the shell form used `-o show.%(ext)s`. Either is a likelier trigger than Popen itself. **Unverified** — reproducing needs a real live broadcast. `internal/scripts/README.md` has the isolation procedure but is gitignored (local only). `dl-live` is undocumented in the public README while this is open.

## FFmpeg practices

- Stream copy (`-c copy`) by default for cut/concat (no quality loss).
- `--accurate` flag re-encodes for frame-accurate cuts.
- `-movflags +faststart` automatically added for MP4 outputs (`_add_faststart_if_mp4`).
- Aspect-preserving resize uses `-2` (even dimensions), not `-1`.
- GIF conversion uses palette generation for quality.
- Replaced deprecated `-async 1` with `-af aresample=async=1`.
- Hardware acceleration: `--hwaccel` flag uses VideoToolbox on macOS (h264_videotoolbox / hevc_videotoolbox).
- Single-frame grab (`screenshot`): ffmpeg extracts one lossless frame via `_grab_frame`; PIL re-encodes jpg/webp through `image_ops.convert_image` (one quality scale 1-100, and works even when ffmpeg is built without libwebp). Fast input `-ss` by default, output `-ss` for `--accurate`. **Gotcha:** when `-ss` overshoots the input, ffmpeg's image2 muxer writes zero frames and still exits 0, so `_grab_frame` verifies a non-empty file was produced and raises `FfmpegError` otherwise.
- Always work with the latest ffmpeg documentation and features.

## Path privacy

Every path written into repo content must be relative to the repo root. Anything
resolving outside it -- another checkout, a home-directory path, an agent-state
directory -- is a leak, in code, comments, docs, commit messages and branch
names alike.

`pre-commit` and `commit-msg` hooks enforce this and hard-block on a finding. Do
not bypass with `--no-verify`; fix the path and re-stage.

**The hooks live under `.git/hooks`, which git does not track, so a fresh clone
has no protection until they are reinstalled.** Run the `path-privacy` skill's
`install-git-hooks.sh`; the same directory holds `find-external-paths.sh` for an
on-demand audit of the working tree.

A line that genuinely needs an external-looking path (regex source, a worked
example) can carry the literal opt-out token the scanner looks for -- see the
skill's own docs. Use it sparingly.

When fixing a leak, keep it quiet: vague commit message, no changelog entry
naming what was scrubbed. Advertising the fix defeats it.

## Versioning

When bumping version, update three places: `vidtools/_version.py`, `pyproject.toml`, `CHANGELOG.md` (semver, no dates, no major bumps without permission).

## Important notes

- Input and output paths are used as the caller supplied them; relative paths work fine. The exception is where ffmpeg itself resolves paths relative to something other than the working directory — `concatenate_videos` and `slideshow` write `os.path.abspath(...)` entries into the concat-demuxer list file, which ffmpeg resolves relative to the *list file*. (This is about runtime arguments only. Paths written into repo *content* must be repo-relative — see Path privacy above.)
- FFmpeg commands run via subprocess with proper error handling (FfmpegError on non-zero exit).
- **AVIF needs no optional dependency.** Pillow ships AVIF encode/decode natively from 11.3, which is the declared floor; `compress.py` probes with `PIL.features.check("avif")` and degrades to the formats the build has rather than failing. HEIC/HEIF input is **not** supported — nothing in the codebase registers a HEIF opener, so installing `pillow-heif` alone would not enable it.
