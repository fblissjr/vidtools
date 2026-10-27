# Defect history

The incidents behind the rules in `AGENTS.md`. `AGENTS.md` says what to do;
this file says why, so the rules can be judged rather than obeyed blindly.
`CHANGELOG.md` remains the per-release record.

## Finder Quick Actions

The feature has failed in at least seven distinct layers, each one different
from the layer the previous fix touched. That pattern is the reason
`AGENTS.md` insists on checking the OS's view rather than the layer just
edited.

1. **Shell semantics.** `/bin/bash` on macOS is 3.2.57, where `set -u` plus an
   empty-array expansion is a fatal "unbound variable". It killed exactly the
   one image preset that passes no flags. The scripts are zsh for this reason,
   and the tests execute them by path so a regression to bash is visible.
2. **Output discovery.** The image action globbed for its output and picked up
   a stale file from an earlier run that had chosen a different format, so it
   reported a saving against the wrong file. It now parses the `wrote ...`
   line; the video action encodes to a temp file and moves it into place.
3. **Plist contents.** A metadata subset that looked plausible installed
   cleanly, passed `plutil -lint`, registered nothing and never appeared in
   the menu. `make_workflow.py` now carries the key set observed in a Quick
   Action Automator itself wrote.
4. **Bundle lifecycle.** Overwriting files in place left the bundle directory's
   mtime unchanged, so macOS kept its cached registration and the action
   dropped out of System Settings while still working from the right-click
   menu. The installer now swaps in a fresh directory.
5. **The environment underneath.** An editable install pointed at a package
   directory that no longer existed. The action was correct and had nothing to
   call; `-x` on the binary still passed. Hence the `--help` pre-flight.
6. **The enable record** (described in `AGENTS.md` as the seventh layer). A
   complete, registered bundle stayed invisible in both the Finder menu and
   System Settings because nothing had written its `NSServicesStatus` entry in
   the `pbs` preference domain. No inspection of the bundle could find it: the
   image and video bundles had byte-identical key sets, and the video one
   matched Apple's own shipping `public.movie` Quick Action.

Other incidents from the same feature:

- **The video action's output counter started at 1 and overwrote previous
  runs**, contradicting `compress._unique_path` and the README. The shell
  implementation now uses the same spelling (`-2` for the second result).
- **Mutation testing the video action.** Ten deliberate defects were written
  back into the script. Two did not turn a test red: one exposed a real bug
  (`status=` is read-only under zsh and aborts the script), and one test
  located the temp file by a marker its own mutation removed, so it passed
  while blind to the defect it named.
- **An `&&` guard broke the install trap.** Rewriting `rm -rf "$X" || true` as
  `[ -n "$X" ] && rm -rf "$X"` moved the `rm` into the position `set -e` acts
  on, so a failing rm aborted the EXIT trap and skipped the bundle restore --
  exactly when a failing rm is what is being recovered from.
- **Removal reported success over something it left behind.** An early
  `[ -d "$dir" ] || return 0` in `remove_generated_bundle()` treated a plain
  file at the bundle path as removed.
- **A zsh dotfile restored PATH underneath the tests.** zsh sources the
  per-user env file for scripts too, which defeated both tests asserting
  ffmpeg is absent until the harness pointed `ZDOTDIR` at an empty directory.

## Imaging

- **0.17.0: six defects, one missing abstraction.** Orientation, ICC profile,
  alpha flattening, container mode limits, quality passing and metadata policy
  were each handled (or not) per call site, which is why the six bugs were
  mutually inconsistent. `_open_image` / `_save_image` replaced them.
- **0.18.0: the probe crop vetoed good candidates.** `compress` let the
  high-variance probe crop reject candidates, and it vetoed 7 of 18 real
  images. Full-image SSIM is now authoritative.
- **0.21.0: `compress` opened files with a bare `Image.open`** and rendered
  portrait phone photos sideways.
- **0.30.0: `slideshow` did the same thing** from `video_ops`, outside the
  module the rule was written for. The rule now applies everywhere.
- **0.31.0: `compress` encoded candidates without calling `_build_exif`** and
  never exposed `--preserve-metadata` on the CLI, so even though `_open_image`
  returned `raw_exif`, every candidate encode dropped it. Both `compress_image`
  and `compress_video` now wire `preserve_metadata` end to end.
- **0.31.0: 2-pass 10-bit `libx265` requires `-pix_fmt yuv420p10le` on Pass 1
  as well as Pass 2.** Omitting it on Pass 1 when the input is 8-bit causes
  Pass 2 to abort with exit code 183 (`different bit depth / csp in stats
  file`).

## Errors reaching the user as tracebacks

- PIL's own exceptions (`DecompressionBombError`, `UnidentifiedImageError`) are
  not caught by `main()`; they printed absolute paths until `_open_raw` /
  `_open_image` translated them.
- **0.30.0:** `get_video_info` / `get_audio_info` ran ffprobe with
  `check=True`, so `vidtools info` on an unreadable file crashed with
  `CalledProcessError`. Both now go through `utils.probe_json`.

## Tests that passed without testing anything

- `test_flip_horizontal` asserted only that dimensions survived, which a no-op
  also satisfies.
- `test_normalize_builds_loudnorm` mocked a stderr shape ffmpeg never emits, so
  for its whole life it exercised the single-pass fallback while being named
  for the two-pass path.
- Two Quick Action tests were satisfied by a shebang string rather than the
  behaviour.
- `pytest.raises(Exception)` on the probe functions accepted the raw
  `CalledProcessError` that crashed the CLI.
- A module-level ffmpeg `skipif` in `test_integration.py` skipped PIL-only
  behaviour tests on machines without ffmpeg while the suite exited 0. Skipping
  is now marker-driven.

Review subagents without a shell have also produced plausible findings from
documentation alone: one pass claimed a glob-metacharacter bug and an argparse
leading-dash bug (both refuted by running them) alongside a real bash 3.2
empty-array crash.

## Documentation drift

- `README.md` claimed three verbs still leaked metadata for three versions
  after they were fixed, because it duplicates the verb list in `AGENTS.md`.
- **0.30.0:** the pipeline's resize step redeclared the verb's flags by hand and
  drifted -- no `--algorithm` choices, no mutual exclusion between sizing
  flags. Both parsers now share `_add_resize_flags` / `_add_crop_flags`.

## Removed features

- **0.16.0:** the FastAPI + WebSocket web UI.
- **0.30.0:** the `preset` command. It read and wrote `presets/` relative to
  the current directory, creating stray folders wherever it ran, and overlapped
  with `convert --quality` and the Finder presets. `mix_audio`,
  `detect_silence` and `remove_silence` went at the same time: exported and
  tested, but reachable from no command.

## Toolchain

- This machine ran an x86_64 ffmpeg under Rosetta until 0.29.0. It works, but
  cannot open a VideoToolbox compression session, so `--hwaccel` degraded to
  nothing with no error naming the cause.

## `dl-live` diagnosis

The long-standing diagnosis of the `Errno 63 File name too long` failure --
"the same flags work from a shell, so Popen is the trigger" -- was wrong, and
the wrongness is why it stalled: Popen has no `shell=True` and no env
overrides, so there was nowhere to look. Diffing the two commands on
2026-07-20 showed they were never the same: the library adds
`--print after_move:filepath` and passes a literal `-o show.mp4` where the
shell form used an `-o show.%(ext)s` template.
