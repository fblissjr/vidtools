# Changelog

## 0.30.1

### Fixed
- **Compress Image no longer raises a critical "Could not compress 0 file(s)" alert** when the selection holds no regular files (only folders, a broken alias, or something deleted before the run). It now exits quietly, matching Compress Video, which already had this guard. Re-run `macos/install.sh` to pick it up.
- **The test suite no longer strands an undeletable directory in pytest's temp root.** `test_a_failed_swap_never_leaves_a_destroyed_bundle` locks a file, and the install it drives moves that file into a `.previous` backup; the test only unlocked the bundle path, so every run left the backup behind and printed cleanup warnings.

## 0.30.0

### Fixed
- **`slideshow` renders portrait phone photos upright.** It opened images with a bare `Image.open`, so a photo stored as a landscape buffer plus an orientation tag was both sized and rendered sideways. It now goes through `_open_image`, like every other path that renders pixels.
- **`vidtools info` on an unreadable file reports an error instead of crashing.** `get_video_info` and `get_audio_info` ran ffprobe with `check=True`, and the resulting `CalledProcessError` (or `FileNotFoundError` for a missing ffprobe) escaped `main()` as a traceback. Both now share `utils.probe_json`, which raises `FfmpegError`. `match-duration` probes through the same path.
- **The pipeline `resize` step validates its flags like the `resize` verb.** It accepted any `--algorithm` string and let `-p`/`-W`/`-H`/`--max-edge` be combined, silently using one. The verb and the step now share one flag definition (`_add_resize_flags`, and `_add_crop_flags` for crop).

### Removed
- **The `preset` command** (`apply`/`list`/`delete`/`edit`), `apply_preset`, `vidtools/presets.py` and `presets/ffmpeg_presets.json`. It read and wrote a `presets/` folder relative to the current directory, creating stray folders wherever it ran and ignoring the repository's own preset file. `convert --quality`/`--video-codec` and the Finder presets cover the same ground.
- **`mix_audio`, `detect_silence`, `remove_silence`.** Exported and tested, but no command reached them.
- The tracked `data/` placeholder directories left over from the web UI; `data/` is now simply ignored.

### Changed
- `CLAUDE.md` now holds rules and traps only; the incidents behind them moved to a tracked `docs/history.md`.
- Applied ruff's safe auto-fixes (import order, unused imports).

## 0.29.0

### Added
- **Finder Quick Action for video** (`Compress Video`). Right-click videos, pick a result, get compressed copies alongside the originals. Six presets: *Smallest — 480p* / *Small — 720p* / *Balanced — 1080p* / *Original size, just compress* / *Strip metadata only — no re-encode* / *Balanced — 1080p, keep metadata*. Verified end to end through the installed bundle: a 1080p clip came out 720p, 8.4x smaller, with title, comment and GPS gone.
  - **A second bundle, not a second preset list.** One bundle accepting both `public.image` and `public.movie` would show the entry on every file and would have to guess at a mixed selection, which has no correct preset list. Two bundles means each entry appears only where it applies.
  - **`--max-edge` exists because the presets would otherwise upscale.** `-W`/`-H`/`-p` are unconditional — right for a verb the user typed against a file they picked, wrong for a preset applied to whatever was selected, where "make this smaller" must not return a file that is *larger* than the original and no sharper. The cap also fits inside a square box rather than capping height, so portrait and landscape get the same treatment: a plain `-H 720` reads correctly on landscape and cuts portrait phone video to 406x720.
  - `force_divisible_by=2` is not decoration. The derived edge is arbitrary, so an ordinary input (1918x1080 capped at 1280) yields an odd height, and yuv420p cannot store one — the encoder fails outright rather than rounding.
  - *Strip metadata only* is a stream copy: instant, pixel-identical, and it keeps the source container. Copying an arbitrary codec into mp4 is not always legal, and the preset's whole point is to touch nothing but the metadata.
  - Encodes are written to a temp file beside the destination and moved into place on success. Video encodes take minutes and are interruptible, so a partial file under the final name is a normal outcome rather than an exotic one — and it also makes the stale-output bug the image action shipped structurally impossible here rather than merely guarded.
  - **libx264, not VideoToolbox.** Measured on an M2 Ultra with ffmpeg 8.1.2, 20s of 1080p down to 720p: x264 took 1.4s for 1.53 MB, `h264_videotoolbox` 0.9s for 5.07 MB. Hardware encoding buys almost nothing at this scale and costs 3x the size, and CRF holds quality steady across content where a fixed bitrate cannot.
- **`resize --max-edge N`** on the `resize` verb and the `pipeline` resize step, for video and images alike. Caps the longest side, never enlarges, orientation-agnostic.
- The video action bakes ffmpeg's directory into its PATH at install time. Automator's PATH contains neither `/usr/local/bin` nor Homebrew's prefix, so without this every preset fails on every file. The image action never needed it — `compress` is pure PIL — which is why this surfaced only now. With no ffmpeg on PATH the installer skips the video action with a warning and still installs the image one, rather than blocking a Quick Action that has no ffmpeg dependency over one that does.
- `make_workflow.py` now refuses a *renamed* placeholder, not just a missing one. Substituting a token that is not there was already caught; a token nothing will substitute shipped as literal text. This is the gap the installer-side check provably cannot cover — it asserts `@VIDTOOLS_BIN@` is absent from the output, which a rename satisfies trivially.

### Fixed
- The two Quick Action bundles no longer share action UUIDs. They were literals from when only one bundle existed.

### Changed
- **`install.sh` no longer deletes recursively.** It removed four directory trees with `rm -rf`, on paths built from variables, running against the real Services folder. Recursion bought nothing: every directory it removes — an installed bundle, a staging dir, a moved-aside backup — holds exactly the two files `make_workflow.py` generated. So removal is now `rm -f` on those named files followed by `rmdir`, which refuses any directory that still holds something and therefore cannot cascade. A bundle with a file the installer did not create is left standing and reported, where `rm -rf` would have taken it without a word. A containment check refuses any path outside the Services directory. The only deletions left anywhere in `macos/` are single named files and two `rmdir` calls.

### Fixed before release (review pass)
Seven findings, all reproduced before acting on any of them. None shipped.

- **`set -e` was aborting the installer's EXIT trap partway through.** `cleanup()` removes the staging directory and *then* restores the previous bundle; written as `[ -n "$STAGE" ] && rm -rf "$STAGE"`, the rm is the last command of an AND-list, so a failing rm ends the function before the restore runs — precisely when a failing rm is the thing being recovered from. The outcome would have been the one the staging design exists to prevent: nothing installed and the old bundle stranded under a dotted backup name. Every step in the trap is `|| true` again.
- **The failure alert would have been a wall of progress bar.** `vidtools pipeline` installs a tqdm sink that redraws with carriage returns, so a real failing run leaves ~1.3 KB of stderr containing exactly *one* newline — `tail -n 3` returned the entire bar with the cause pushed past what the dialog shows. That is the 0.28.0 regression ("the alert named no cause") arriving by a new route, and it is video-only, since `compress` never reports progress. stderr is now split on carriage returns with bar frames dropped.
- **A negative `--max-edge` silently wrote a 1x1 file.** `--max-edge -100` on an 800x600 image exited 0 having produced a single pixel: the negative scale factor fell through to the `max(1, ...)` clamp that exists for genuinely tiny images. `0` was also read as "not supplied" and reported that no sizing option had been given. Both now raise.
- **Two selected files with the same stem overwrote each other.** Every re-encode preset forces `.mp4`, so `clip.mov` and `clip.mp4` both resolved to `clip_compressed.mp4` — the second silently replacing the first while both were counted. Worse, selecting `clip.mp4` alongside an existing `clip_compressed.mp4` destroyed the latter before it was read. Outputs are now claimed against the whole selection and suffixed on collision.
- **The video action's output naming contradicted `compress`.** `compress._unique_path` never lets a derived output replace an existing file — a second run writes `<stem>_compressed-2.<ext>` — and the README documents that as a property of the tool. The video action started its counter at 1 and happily overwrote the previous run. It now applies the same rule with the same spelling, so two right-clicks on one video behave like two `compress` invocations on one photo.
- **Bundle removal reported success over something it left on disk.** `[ -d "$dir" ] || return 0` read anything that is not a directory as "nothing to do" and returned success, so a plain file sitting where a bundle belongs printed `removed: Compress Image` and exited 0 while untouched — the opposite of the function's stated contract, which is that non-zero means something was left behind. It also takes `${1:-}` rather than `$1`, since an unbound positional under `set -u` aborts the caller and one caller is the EXIT trap.
- A selection containing no regular files raised a *critical* alert reading "Could not compress 0 file(s)." It now exits quietly.
- Five installer tests assert the video bundle exists, which the installer only builds when ffmpeg is present — so they failed for an environment reason on any machine without it. They carry `@pytest.mark.ffmpeg` now, which is what the marker convention is for. The test asserting the *skip* is deliberately unmarked.
- `make_workflow.py`'s docstring named `_WORKFLOW_METADATA`, replaced by `build_metadata()` in this same change.

### Fixed after first real install
- **The video action registered correctly and appeared nowhere.** Complete bundle, clean `plutil -lint`, listed by `pbs -dump_pboard` with the right `NSSendFileTypes` and macOS's own assigned UUID — and absent from both the Finder menu and System Settings → Login Items & Extensions → Finder, so there was no toggle to switch it on either.
  - The bundle was never the problem. It had a **byte-identical key set** to `Compress Image`, which worked; it matched a user-installed `Resize Video` on everything but the type filter; and it matched Apple's own shipping `Encode Selected Video Files` on `NSSendFileTypes = public.movie` and `NSRequiredContext`. `.mp4`, `.mov` and `.mkv` all conform to `public.movie`, checked via `kMDItemContentTypeTree`.
  - The only asymmetry was an entry under `NSServicesStatus` in the `pbs` preference domain — the record the System Settings toggle writes. Every action that worked had one; the new one did not. `macos/enable_service.py` now writes it at install time, and `install.sh` prints `killall Finder` as the follow-up.
  - **Add-only, never overwrite.** An existing entry is the user's own preference, including a deliberate "off", and a reinstall must not overturn it.
  - Non-fatal by design: the bundle is on disk before this runs, so a failure here is a note rather than an aborted install. Staging that failure needed the script itself broken — `defaults import` exits 0 even when it cannot write, which made the first version of that test pass while exercising nothing.
  - Red herrings, both confirmed meaningless against controls: `lsregister -f` reports `-10811` on every `.workflow` including working ones, and `pbs -dump_pboard` reads 0 for a few seconds after `killall pbs` while it rescans.

### Fixed (security review of the registration fix)
Four findings, all reproduced by execution before acting. The first two were the fix's own failure modes.

- **`enable_service.py` reported success over a write that never landed.** `defaults import` exits 0 whether or not it wrote anything — verified against an unwritable directory, where the script printed `enabled: Test Action`, exited 0, and stored nothing. That put the user back in exactly the invisible-action state this module exists to prevent, while telling them it was fixed. It now reads the domain back and confirms the key is present.
- **The test suite was writing into the user's real `pbs` preference domain.** `install.sh` falls back to `${VIDTOOLS_PBS_DOMAIN:-pbs}`, and the two long-standing helpers that run the installer never set it — so every install test performed a read-modify-write of live preferences, adding enable records for bundles that existed only under `tmp_path`. Only the add-only rule kept it from doing damage. Both helpers now route through one `_test_env`, and a test fails if any environment is built without the override.
- **A failed *read* would have erased every other Quick Action's toggle.** `defaults export` emits a full `<dict/>` plist even for a domain that never existed, so empty output means the read failed — but the code treated it as an empty domain and wrote a `NSServicesStatus` rebuilt from nothing. Since `defaults import` merges at the top level and *replaces* nested keys (verified), that would have dropped every other service's entry. An empty read is now a refusal.
- A test asserted on a substring its own stub also emitted, so it could not detect deletion of the installer message it named. Confirmed by mutation: swapping the note for `|| true` left it green. The stub's message and the assertion are now distinct.

### Documentation
- The README's macOS prerequisites now say that `ffmpeg-full` is **keg-only**. `brew install ffmpeg-full` on its own changes nothing, because Homebrew does not link it — the prefix has to go on PATH explicitly, or the capability probe keeps reporting the slim build's gaps. It also shows how to check that `ffmpeg` is arm64: an x86_64 build under Rosetta on Apple silicon runs fine but cannot open a VideoToolbox compression session, so `--hwaccel` degrades to nothing with no error naming the cause.
- `--max-edge` is documented with a measured before/after table rather than prose. The first draft of that table was wrong in both cells — `-H 720` and `--max-edge 720` are *identical* on portrait, and the interesting divergence is on landscape — which is a good argument for measuring examples rather than reasoning them out.

### Testing
- `tests/test_macos_video_action.py` drives the video script end to end with stubs, and every claim in it was verified by reintroducing the defect and confirming red. That caught two real problems: the script assigned to `status`, which is **read-only in zsh** (the spelled-out form of `$?`) and aborted the script outright; and a test that located the temp file by a marker its own mutation removed, so it passed while blind to the defect it claimed to catch.
- The same mutation pass over the installer covers all eight of its claims.
- The video test harness points `ZDOTDIR` at an empty directory. zsh sources its per-user env file for *every* invocation, scripts included, so a dotfile setup that composes PATH silently restored a full PATH underneath the two tests that assert ffmpeg is absent.

## 0.28.0

### Changed
- **Python floor raised to 3.14.** `requires-python`, the trove classifiers, ruff's `target-version` and mypy's `python_version` all move together, and a `.python-version` file now pins the interpreter so `uv sync` cannot quietly build a venv on an older one. Raising the target introduced no new lint findings — ruff reports an identical count under `py312` and `py314`.

### Fixed
- **The Quick Action reported failure without reporting a cause.** `compress`'s stderr went to `/dev/null`, so when the venv's editable install pointed at a package directory that no longer existed, the alert said only "Could not compress 1 file(s)" and Automator followed it with an error dialog whose message was the empty string. The first failure's stderr is now carried into the alert.
- **A dead binary passed for a working one.** Both `install.sh` and the embedded script tested the binary with `-x`, which proves a file is executable and nothing more. An editable install whose package is missing satisfies that and exits 1 on every call, so the installer printed `installed:` over it and deferred the failure to right-click time. Both now check that the binary actually starts, and say what to run.

### Added
- Alert and notification text is escaped before it reaches AppleScript. An unescaped `"` or `\` does not garble the dialog, it makes the whole script a syntax error so nothing is shown at all — which would have been the failure mode for the code path whose only job is reporting failure, now that it carries error text and filenames rather than fixed strings.
- The installer builds into a staging directory and swaps it into place. Previously it deleted the installed bundle before it knew whether the replacement would build, so any failure during generation left nothing installed. The swap is still a directory replace, which is what forces macOS to re-scan. The old bundle is renamed aside rather than deleted, and restored if the swap does not complete — deleting it outright looked atomic but was not, and a delete that failed partway (one immutable or locked file is enough) took out the installed action while the cleanup trap discarded the finished replacement.
- The installer uses the venv's own interpreter rather than whichever `python3` PATH surfaces first. The venv is already a hard precondition, so its Python is the one guaranteed to be there.
- `make_workflow.py` refuses to generate a bundle when the `@VIDTOOLS_BIN@` placeholder is missing. Substituting a token that isn't there is a silent no-op producing an action with no path to the binary, and the installer-side check cannot catch it — it asserts the placeholder is absent from the output, which a *renamed* placeholder also satisfies. It now also reads the script as explicit utf-8, since the AppleScript block carries non-ASCII continuations and the default encoding is locale-dependent.
- A compress that exits 0 without naming an output file counts as a failure rather than a success. `ok` now means "a file was written", not "the process exited 0".
- ruff's `select`/`ignore` moved under `[tool.ruff.lint]`. They were still honoured at the top level, but every invocation printed a deprecation warning. Finding count is unchanged.

## 0.23.1

### Fixed
- **The Quick Action's most neutral preset aborted before doing anything.** The embedded script ran under `/bin/bash`, which on macOS is still 3.2.57 even on 26.x — and there `set -u` combined with expanding an empty array is a fatal `unbound variable`. "Balanced (WebP)" is the one preset that passes no flags, so it built an empty argument array and died. It now runs under `/bin/zsh` (5.9, the platform default), which handles it.
  - The end-to-end test that "verified" 0.23.0 drove a preset that *does* set flags, so it never touched the broken path.
- Output size is now found by globbing rather than parsing `ls` output, so filenames containing spaces, quotes, or glob metacharacters (`my photo's "best" shot.png`, `weird[1]name.png`) survive.

## 0.23.0

### Added
- **Finder Quick Action for macOS** (`macos/install.sh`). Right-click images, pick a result, get compressed copies alongside the originals. Verified end to end: an 11.3 MB source came out at 84 KB.
  - **One menu entry opening a preset list**, not one entry per operation — the convention macOS's own Convert Image follows, and it stops the Quick Actions menu filling up. Presets are outcome-shaped (*Smallest, sized for AI vision* / *Balanced* / *Visually lossless* / *Lossless* / *Balanced but keep metadata*) rather than flag-shaped, because someone right-clicking a file is choosing a result.
  - The workflow bundle is **generated at install time, not checked in**: the embedded script needs the machine's absolute path to the `vidtools` binary, which must not appear in the repo, and a `.wflow` is an opaque plist that reviews badly. The reviewable artifact — `macos/compress-image.sh` — stays in plain sight with a `@VIDTOOLS_BIN@` placeholder.
  - Automator rather than a native app, so there is no signing or notarisation step. The trade is that AppleScript cannot draw a multi-control panel, so it is a single preset list instead of the Format / Size / Preserve Metadata trio. That script is what a native front end would call if the trade stops being worth it.
  - Calls the venv binary directly rather than `uv run` (measured 0.11s vs 0.13s startup) and every other binary by absolute path, because Automator runs with a minimal PATH.

## 0.22.0

### Added
- **`compress --fit vision`** downscales an image to what the destination will actually keep, before compressing it. On the 6144x2560 reference image: **135x smaller instead of 34x** (329 KB to 84 KB), with the model seeing the same pixels either way — it would have resampled to 2576x1073 on arrival regardless.
  - Two profiles: `vision` (Opus 4.8/4.7, Sonnet 5 — 2576px long edge, 4784 visual tokens) and `vision-standard` (Haiku and older — 1568px, 1568 tokens). Named rather than a raw pixel flag because the constants differ per model. Never upscales.
  - **Both constraints are enforced, and a long-edge cap alone is wrong** for the most common photo shapes: fitting a 3:2 photo to 2576px still leaves it 19% over the token budget and a square image 77% over, so the destination resamples a second time — discarding detail you paid to compress and adding a resampling pass.
  - The token cap is solved in *patch* space, not pixel area. A visual token is a 28x28 patch counted with ceiling division, so a partial patch costs a whole one: 2372x1581 sits under 4784x28x28 pixels but is 85x57 = 4845 patches. A pixel-area calculation overshoots on exactly the aspect ratios where the budget binds.
  - `CompressionReport` gains `encoded_width`/`encoded_height`/`fit_profile`; `width`/`height` still mean the source, so existing callers do not silently change meaning.

### Scope, measured
Across 2,026 local images, **14% exceed the high-resolution tier** and 41% exceed the standard one — about 1.7 gigapixels currently discarded on arrival in aggregate. So this is a no-op for most images and a large win for large ones; it is opt-in for that reason rather than being folded into the default.

### Fixed
- `compress` routes through the shared imaging core, so EXIF orientation is applied and the ICC profile is carried into every candidate encode. It previously used a bare `Image.open`, which rendered portrait phone photos sideways.

## 0.21.0

### Added
- **Audio metadata policy — and it deliberately inverts the image/video default.** Those strip everything because their metadata is incidental; for audio it is frequently the point. Artist, title and album are what make a library work, and `convert song.mp3 song.m4a` handing back an untitled file would be a worse bug than the leak. So the same three-category model applies with different membership: descriptive tags (artist, title, album, track, genre, date) are **kept**, free-form and recorder fields (`comment`, `description`, `encoded_by`, `location`, `make`, `model`) are **dropped**. `comment` is the field that held 109 KB of generation prompt in the video case. `--preserve-metadata` keeps everything.
  - Applies to `convert`, `trim`, `normalize`, `fade`, `resample`, `clean`, `mix` and `remove-silence`. Verified: every audio op previously carried artist, title, comment, album and date straight through.

### Fixed
- **`compress` no longer renders portrait photos sideways or drops colour profiles.** It opened images with a bare `Image.open` rather than the shared imaging core, so it skipped EXIF orientation and the ICC profile — reintroducing, through a second open path, exactly the defects 0.17.0 fixed in `image_ops`. Confirmed against a phone photo: a portrait source came out displaying landscape.
- `subtitles` exposes `--preserve-metadata`; it applied the policy but offered no way to opt back in.
- Raised the `Pillow` floor from `>=9.0.0` to `>=11.3.0`. The old floor would crash: `Image.Resampling`/`Image.Palette` need 9.1, and `compress` probes `features.check("avif")`, which needs 11.3 for native AVIF.

### Removed
- `requirements.txt` — a legacy duplicate of the `pyproject.toml` dependency list, referenced nowhere and still carrying the stale Pillow floor. A second source of truth for dependencies is exactly the drift this release is cleaning up.

### Docs
- `CLAUDE.md` and `README.md` brought in line with the code: removed the deleted `deploy/` section and the web-op reference, added `compress.py` and the imaging core to the architecture, corrected the test-file inventory and documented the `ffmpeg`/`pil_real` markers, and documented the metadata policy across all three media types with its remaining gaps stated rather than glossed.
- Corrected a CLAUDE.md claim that `pillow-heif` was a supported optional dependency. Pillow 12 does AVIF natively and nothing registers a HEIF opener, so installing it would enable nothing. HEIF is unsupported; the docs now say so.

## 0.20.1

All of the below were found by review of the 0.16.0-0.20.0 work and verified by execution before and after.

### Fixed
- **`strip-metadata` no longer re-quantizes JPEGs.** Pillow's encoder defaults to quality 75 when the caller omits `quality`, so stripping metadata from a quality-95 photo halved it — 119 KB to 57 KB, with 92% of pixels changed — while the docstring claimed it altered none. The source's quantization tables and subsampling are now carried across. A JPEG round-trip is still not bit-exact (it is a decode/encode, not a container edit) and the documentation now says so rather than overclaiming.
- **Colour survives GIF output.** `.gif` declares modes `{L, P}` and no `RGB`, so the fallback order tried greyscale before palette and silently desaturated every colour image saved as `.gif`.
- **Transparency flattens onto white, not black.** The composite step existed but was unreachable: the colour-mode conversion ran first and did a bare `convert("RGB")`, which discards alpha and exposes whatever sits underneath — (0,0,0) in real transparent PNGs. Affected `.jpg`, `.bmp` and `.gif` output.
- **JPEG comment markers no longer leak.** Pillow reads `comment` from `im.info` when the caller supplies none, bypassing the EXIF allowlist entirely. Free-form text is the same payload class that appeared as 109 KB of generation prompt in video comment tags.
- **`pipeline` applies the metadata policy.** It ran the same operations as the individual verbs but stripped nothing, so `vidtools pipeline in.mp4 out.mp4 trim ... resize ...` carried GPS through while `vidtools resize` on the same file removed it — reachable by the route a user takes to *avoid* re-encoding.
- **`--preserve-metadata` reaches every verb that supports it.** `concat`, `add-audio`, `match-duration` and `pipeline` had the underlying parameter but never exposed the flag, so 0.20.0's claim that they were covered was only half true.
- Video metadata stripping now passes `-map_metadata:s -1` as well as `-map_metadata -1`, matching `sanitize`. In testing the bare form already cleared per-stream tags on a stream copy, but only the global scope is documented behaviour, and a privacy default should not rest on that.
- `add-subtitles` applies the metadata policy.
- Removed stale web-UI references missed by 0.16.0: the package description, a `package-data` glob pointing at the deleted `web/`, and two module docstrings.

### Known gaps (not fixed here)
- **`audio_ops.py` has no metadata policy at all.** ID3/Vorbis tags — artist, comment, encoder — pass through `convert`, `trim`, `normalize`, `fade`, `resample` and `clean` untouched. Same class as the video leak fixed in 0.20.0; deferred rather than rushed, since it is a separate surface with its own tag semantics.
- `extract-audio`, `extract-frames` and `slideshow` still carry source metadata.

## 0.20.0

### Changed (privacy)
- **Video ops no longer carry identifying metadata into their output.** `trim`, `convert`, `resize`, `crop`, `rotate`, `concat`, `match-duration` and `add-audio` all passed the source's container tags straight through, so a trimmed phone video shipped its **GPS coordinates**, camera make/model and capture time. Only `sanitize` stripped them. Verified against a tagged clip before and after.
  - This is the mirror of the image bug fixed in 0.17.0: image ops dropped everything including data they should have kept; video ops kept everything including data they should have dropped.
  - Locally generated files showed the payload is not just location — AI video tools embed their full workflow and prompt text in the `comment` tag, measured at 41 KB and 109 KB on two real files.
  - `--preserve-metadata` opts back in.

### Why this was safe to default on
Rotation is unaffected, which was the open question blocking this change. Verified against a video carrying a 90-degree display matrix: on a **stream copy** the matrix survives `-map_metadata -1` intact; on a **re-encode** ffmpeg auto-rotates during decode, so the pixels come out upright and the matrix is no longer needed. Source, copy and re-encode all display identically.

Note the granularity differs from images by necessity. For images the kept set is an allowlist, so `--preserve-metadata` restores authorship tags without re-admitting GPS. ffmpeg's `-map_metadata` is all-or-nothing at the container level, so for video the flag keeps everything, location included.

## 0.19.1

### Fixed
- **Animated images are refused instead of silently flattened.** `.gif` was routed to the video path for exactly this reason, but `.webp` and `.png` were not — so an animated WebP went through the image ops and kept only frame 1. A real 239-frame, 20 MB file came out as a single 52 KB still with no warning. The guard is keyed to format as well as `is_animated`, because MPO (iPhone HDR) also reports `is_animated` and its second frame is a gain map, not animation — taking frame 1 there is correct.
- **Cartoon and anti-aliased vector art now classify as flat.** The classifier leaned on unique-colour count, which breaks on anti-aliased content: every edge generates intermediate shades, so frames from one animation measured 12k–26k colours and near-identical frames split between the two classes. Six of seven got the loose target and were compressed to **q25–q29**; all seven now land at **q81–q93** (10x–18x reduction, SSIM ~0.995).
  - Classification now uses flat-run fraction plus **hard-edge density**. The second term is what separates cartoon and text (real edges) from a smooth gradient, which also has many flat runs but no edges and should stay continuous-tone.
  - **The classes genuinely overlap on these features** — photographs measured 0.054–0.388 flat-run, upscaled renders ~0.53, cartoon and screenshots 0.104–0.923. The threshold is set to keep photographs and upscaled renders out, accepting that some flat content is still missed. That is the pre-existing failure, not a new one, but it is a failure: the real fix is a metric that sees edge ringing rather than a better guess at content type.

## 0.19.0

### Fixed
- **Text and UI screenshots are no longer compressed into mush.** Flat content (text, line art, UI chrome) now targets SSIM 0.995 rather than the 0.98 default.
  - SSIM's scale is compressed on hard edges, so the usual target let the search bottom out at the lowest quality it was permitted to try. Measured on a real 1979x1307 terminal screenshot: WebP q25 scored **0.9898** with visibly ringing, mushy glyphs, while q60 — clearly legible — scored only **0.9951**. The entire bad-to-fine range spans 0.005, well inside what a 0.98 target treats as acceptable.
  - Across eight real screenshots the selected quality moved from q25/q30/q35 to q58–q84. Files grew modestly (e.g. 18K to 32K) while reductions stayed at 6.5x–25.6x.
  - This is a stopgap keyed to the content classifier, not a fix for the metric. A metric that actually sees edge ringing is the real answer; screenshots the classifier reads as photographic still get the looser target.

## 0.18.1

### Fixed
Found by broadening validation beyond RGB PNG/JPEG — a synthetic colour-mode/format matrix plus a stratified sample of real local images.

- **CMYK images no longer fail on PNG/WebP output.** `_save_image` handled alpha incompatibility but not colour-space incompatibility, so a CMYK scan raised `OSError: cannot write mode CMYK as PNG`. Each container now declares the modes it can store, and unsupported modes are converted to the nearest representable one.
- **16-bit and bilevel images no longer fail to resize.** LANCZOS rejects `I;16` and `1` outright — `thumbnail` raised `ValueError: image has wrong mode` from the resampling step, not the encoder. Such modes are promoted to a continuous-tone mode before resampling. Palette images are promoted too: resampling P-mode interpolates palette *indices* rather than colours, producing arbitrary hues.
- **`resize --percentage` never produces a zero dimension.** Scaling a very small image rounds to zero and PIL rejects it; a real 1x1 image failed at 25%.
- **`strip-metadata` refuses mode-lossy targets with a clear message** instead of a raw `OSError`. Converting CMYK to RGB to satisfy a `.png` target would alter pixels, which is precisely what this op must not do.
- **`strip-metadata` now keeps the ICC profile.** Dropping it changes how the image renders — a pixel-level change in all but name. This op removes EXIF/IPTC/XMP annotations, not colour semantics.

## 0.18.0

### Fixed
- **`compress` no longer rejects images it can actually compress.** Quality was chosen by binary-searching a 512px crop of the busiest region, and that result was final. But the busiest tile is the *hardest* part of the frame, so it scores below the image as a whole and vetoed encodes that were comfortably achievable. On a sample of 18 real photographs and renders, **7 failed outright** with "No candidate satisfied the constraints" — including one whose full-image SSIM reached 0.985 at a quality the search never tried.
  - The full image is now authoritative; the probe crop only narrows the starting bracket and can no longer veto. All 18 now succeed.
  - The probe also *over*-shot in the other direction. On the 6144x2560 test image the search now selects q78 rather than q86 for the same target — 0.32 MB instead of 0.47 MB, a 34x reduction rather than 23x. Correctness improved the output as well as the success rate.
  - Cost: searching at full resolution is slower (~3s to ~10s on a 16 MP image). That is the price of the answer being right.
- **Unsupported output extensions are rejected instead of double-suffixed.** `compress photo.png photo.bmp` wrote `photo.bmp.jpg`; `.bmp`, `.gif` and `.tif` are all in this project's own `IMAGE_EXTS`, so this was easy to hit. The error now lists the extensions that work.
- **An explicit `.avif` output implies `--allow-avif`.** Naming the extension is an explicit request; the flag exists to stop AVIF being chosen *accidentally*. Previously this failed with an error that never mentioned AVIF.
- **Unreachable targets say what was achievable**: "The best achievable was SSIM 0.9996; try --target-ssim 0.999" rather than a bare refusal.

### Changed
- AVIF encoder effort 6 -> 7, measured both smaller and faster at every quality tested. Pinned with a guard against 8, which inflates output 3.7-7x in Pillow's AVIF encoder specifically (avifenc does not do this).

### Internal
- Replaced a tautological lossless test that asserted `Candidate.ssim == 1.0` — a constant hardcoded in the source, never measured. It now decodes each candidate and compares pixels against the original, and was verified to fail against a deliberately-lossy encoder.

## 0.17.0

### Fixed
All six defects below were verified by execution before and after the fix. The 304-test suite passed against every one of them.

- **EXIF orientation is applied, not discarded.** `Image.open` does not rotate by orientation tag, so a phone photo arrived as a landscape buffer. `crop` therefore operated on pixels the user could not see — cropping a region off a portrait photo hit the wrong part of the image — and every op dropped the tag on save, so output displayed sideways in viewers that had previously corrected it. Orientation is now baked into pixels on open.
- **ICC colour profiles survive.** Profiles were dropped on `.jpg` and `.webp` output but kept on `.png` and `.avif` — not by design, but because Pillow's PNG and AVIF plugins fall back to `im.info` while its JPEG and WebP plugins read the save kwarg only. A Display-P3 image converted to JPEG was silently reinterpreted as sRGB.
- **`strip-metadata` no longer destroys palette images.** It built `Image.new(mode, size)` and pasted into it; for P-mode that allocates a *default* palette, and pasting P-into-P copies raw indices without remapping. A four-colour PNG came out solid black.
- **`--quality` reaches the AVIF encoder.** Only jpg/jpeg/webp were passed a quality argument, so every `.avif` export used Pillow's default of 75. quality=1 and quality=100 produced byte-identical files.
- **Off-axis rotation no longer bakes in black corners.** `Image.rotate` was called without `fillcolor`, so the corners exposed by a non-right-angle rotation were zero-filled — opaque black on any RGB image, with no way to override. Exposed corners are now transparent by default, with a new `--fill` option.
- **Palette and alpha inputs no longer crash on JPEG output.** `convert` flattened RGBA/P/LA before saving; `resize`, `crop` and `thumbnail` did not, so identical input raised `OSError: cannot write mode P as JPEG` depending on which verb you used. Flattening now composites onto white rather than converting straight to RGB, which would composite transparency onto black.

### Added
- **A metadata privacy policy, applied by default.** Identifying metadata — GPS coordinates, camera make/model, capture timestamps — is stripped from output. Rendering-critical data (orientation, colour profile) is always preserved, since it is part of the image rather than an annotation on it.
  - `--preserve-metadata` keeps authorship tags (copyright, artist, description). It does **not** re-admit GPS: provenance and location are different categories.
  - The tag list is an allowlist, so a tag nobody has classified is not a leak.
  - This differs from macOS's Convert Image, which preserves by default. These verbs write a *new* file, so the original retains everything while a leaked location does not come back, and the dominant use here is compress-to-share.
- `adjust --fill` sets the corner colour for non-right-angle rotations (default transparent).
- `info` reports *displayed* dimensions for rotated images, with `raw_width`/`raw_height` when they differ. A portrait phone photo previously reported its landscape buffer.

### Internal
- Added `_open_image` / `_save_image` in `image_ops.py`; all seven ops route through them. The six defects above were one missing abstraction rather than six independent bugs, which is why they were mutually inconsistent.

## 0.16.1

### Fixed
- **Image operations no longer require ffmpeg.** `main()` checked for the toolchain unconditionally, so `vidtools resize photo.png out.png` — pure PIL, no subprocess — refused to run on a machine without ffmpeg installed. The check is now scoped to invocations that actually need it (`_needs_ffmpeg`), with multi-media verbs deciding from the input's kind.

### Internal
- **Test foundation.** Added `tests/conftest.py` (there was none): shared image fixtures, suite-wide progress-sink isolation, and marker-driven skipping. Registered `ffmpeg` and `pil_real` markers with `--strict-markers`.
  - Previously a machine without ffmpeg ran 289 of 304 tests, skipped 15, and **exited 0** — reporting success while dropping every behavioural check in the project. Skipping is now driven by capability rather than file location, and the PIL-only tests run everywhere. The suite reports the same failures with and without ffmpeg.
  - The shared image fixture was a single flat colour, which makes crop/flip/rotate correctness untestable in principle — a real horizontal flip and a no-op are pixel-identical on it. It is now four distinct quadrants.
  - Added `tests/test_imaging_contract.py`: contract tests for EXIF orientation, ICC profiles, palette preservation, format-specific save options, rotation fill, and the metadata privacy policy. **12 of these currently fail** — they assert intended behaviour ahead of the imaging refactor, so that a green suite afterwards means something.
  - Split the two PIL-only CLI tests out of `test_integration.py` into `tests/test_cli_image_paths.py`; living there meant inheriting a module-level ffmpeg gate.

## 0.16.0

### Removed
- **The web UI is gone.** `server.py` (FastAPI + WebSocket), `jobs.py`, `db.py`, `security.py`, `vidtools/web/`, and `deploy/` are deleted — 3,436 lines, along with the 71 tests covering them. The CLI is now the only surface.
  - Drops five dependencies: `fastapi`, `uvicorn[standard]`, `python-multipart`, `python-jose[cryptography]`, `bcrypt`. The resolved dependency graph goes from 118 packages to 95.
  - Rationale: the CLI imported none of it, so it was cleanly separable, and it carried real defects that were never going to be worth fixing — image inputs were routed to ffmpeg's video path because the server had no media-kind detection, and uploads had no MIME allowlist, extension allowlist, or magic-byte sniffing. Its test coverage was largely illusory: zero HTTP-level tests existed (no `TestClient` anywhere), and only 1 of 13 dispatch handlers had any test.
  - A macOS Quick Action is planned as the second surface instead. Git history retains everything if it is ever wanted back.

## 0.15.0

### Added
- **`compress`** — a new image verb that picks the format *and* quality by measuring, not by heuristic. It encodes candidates, scores each against the original with SSIM, and writes the smallest one that meets a fidelity target (`--target-ssim`, default 0.98). Also supports `--lossless` (exact encodes only) and `--max-size 500k` (maximise fidelity within a byte budget). `--dry-run` reports the ranked candidates without writing.
  - Distinct from `optimize`, which recompresses into the format named by the output extension. `compress` chooses the format; give it an output path without an extension (or none at all) and the winner supplies one.
  - **Defaults to PNG/JPEG/WebP, excluding AVIF.** AVIF compressed ~2x better than WebP in testing, but no major vision API decodes it — the Claude API accepts JPEG/PNG/GIF/WebP and Gemini accepts PNG/JPEG/WebP/HEIC/HEIF, and neither converts client-side. Defaulting to AVIF would emit files those tools cannot read at all. `--allow-avif` opts in for storage or browser targets.
  - Quality is searched on a native-resolution crop of the highest-variance region, not a downscaled proxy: downscaling averages away the blocking and ringing the search exists to detect, and a flat-region crop would over-recommend aggressive quality.
  - Pillow-only (no `cwebp`/`avifenc`/`oxipng` subprocesses); a build without AVIF or WebP degrades to the formats it has rather than failing.
  - Caveat, stated in the module docstring: SSIM is a mediocre perceptual metric, chosen because it is computable with numpy alone and is monotonic in encode quality. The default target is eyeball-calibrated, not perceptually principled.

### Fixed
- Content classification no longer misreads smooth or upscaled images as flat art. Flat *runs* alone were treated as evidence of synthetic content, but upscaled photos are full of adjacent-equal pixels simply because smooth gradients quantize to the same 8-bit value — a real 6144x2560 upscaled render measured 69% adjacent-equal with ~29k unique colours. Flat runs now only count when the palette is also small. The misclassification made the search spend ~35s on full-resolution lossless encodes that never win; the same image now completes in ~3s with an identical result.

## 0.14.1

### Fixed
- `convert <video> <audio> --quality N` (e.g. `convert clip.mp4 out.mp3 -q 95`) now honors `--quality`. Audio-only output is routed to the audio encoder (quality→bitrate) regardless of input type; previously a video input went through the video path where `--quality` became an unused CRF and was silently dropped.
- The CLI reports operation validation errors (bad rotation, invalid crop geometry, missing fade args, non-numeric rotate angle) as a clean `Error: ...` with exit 1 instead of a raw traceback — `main()` now catches `ValueError` alongside `FfmpegError`.
- Audio verbs on a video input writing a video output (`normalize`/`fade`/`resample`/`clean clip.mp4 out.mp4`) now produce AAC audio, not raw PCM — the latter muxed into mp4 as a non-standard stream many players reject.
- Web **Clean Audio** on a video upload keeps the video now (processes the audio track and remuxes), matching the CLI; previously it dropped the video stream.
- `rotate` on an image accepts non-integer angles (e.g. `45.5`) and gives a clear message on non-numeric input.

### Internal
- Extracted `video_ops.apply_audio_op_keep_video` as the single audio-or-remux router, shared by the CLI audio verbs and the web `clean` dispatch.
- Moved the media-type extension sets (`VIDEO_EXTS`/`AUDIO_EXTS`/`IMAGE_EXTS`) to `utils.py` as the single source of truth.

## 0.14.0

### Added
- Web UI parity: **Clean Audio** and **Match Duration** are now in the Process dropdown (Screenshot was already there), so the file-in/file-out CLI ops are reachable from the phone. `separate` (multi-output) and `dl-live` (URL/live) intentionally stay out of the single-file form.
- **Real-ffmpeg integration test tier** (`tests/test_integration.py`): runs the actual ffmpeg/PIL toolchain on a generated clip for the high-risk paths (convert→mp3 stream handling, screenshot seek-past-EOF, audio-verb-on-video remux, stream-copy trim, resize, webp-via-PIL). Skips automatically when ffmpeg/ffprobe aren't on PATH. Complements the mocked unit tests, which only verify command shape.

No CLI/op behavior changes.

### Fixed
- `vidtools preset` with no sub-action now prints help instead of crashing with `AttributeError: 'Namespace' object has no attribute 'func'`. `handle_command` also guards against a missing `func` defensively.

### Internal
- Simplification pass over the unified-CLI code: `convert` now derives the output extension with `Path(...).suffix` (consistent with the rest of cli.py); clarified the `crop_video` validation docstring (it checks geometry signs, not full bounds).

## 0.13.0

### Added
- **Audio verbs now accept video inputs.** `normalize`, `fade`, `resample`, and `clean` work on a video: an audio output (e.g. `out.mp3`) extracts and processes the audio; a video output (e.g. `out.mp4`) processes the audio track and remuxes it back over the original video (video stream copied). So `vidtools normalize clip.mp4 out.mp4` fixes the audio and keeps the picture.

### Changed
- `--quality` → CRF mapping recentered so `--quality 50` lands on the x264 default CRF 23 (was ~28); 100 → 16 (near-lossless), 1 → ~30.
- Pipeline `convert` step flags aligned with the top-level CLI: `--video-codec`/`--audio-codec`/`--video-bitrate`/`--audio-bitrate` (were `--vcodec`/`--acodec`/`--vbitrate`/`--abitrate`).

### Fixed
- `convert`/`screenshot` to WebP at `--quality 100` now writes a true lossless WebP (matches "higher = better").
- Web UI quality field parsing uses an explicit radix and guards `NaN`.

## 0.12.0

### Changed (breaking: CLI command surface unified)
- **One verb per concept; media-prefixed commands removed.** The `img-*` and `audio-*` command families are gone. Every operation is now a top-level verb. Multi-media verbs auto-detect the input type: `info`, `convert`, `resize`, `crop`, `trim`, `rotate` all work on whatever you hand them (video/audio/image as applicable) without you choosing a variant.
- **`cut` → `trim`** (canonical), with `cut` kept as an alias. `trim` works on both video and audio.
- **`trim-to-audio` → `match-duration`** (clearer, and out of the "trim" namespace).
- Audio ops dropped their prefix: `audio-normalize`→`normalize`, `audio-fade`→`fade`, `audio-resample`→`resample`, `audio-clean`→`clean`, `audio-separate`→`separate`, `audio-convert`/`audio-cut`/`audio-info` folded into `convert`/`trim`/`info`.
- Image ops dropped their prefix: `img-thumb`→`thumbnail`, `img-strip-exif`→`strip-metadata`, `img-optimize`→`optimize`, `img-adjust`→`adjust`, `img-resize`/`img-convert`/`img-crop`/`img-info` folded into `resize`/`convert`/`crop`/`info`.
- **Unified `--quality` (1-100, higher = better)** on `convert`, mapped per media (video→CRF, image→PIL quality, audio→bitrate). Power-user overrides `--crf` (video) and `--bitrate` (audio) still win. Removed the old hack where `--crf` was reinterpreted as image quality.
- Consistent flag names: `--video-codec`/`--audio-codec` everywhere (dropped `--vcodec`/`--acodec`/`--codec`); `--start`/`--end`/`--duration` for all time ranges. `trim` dropped the vestigial `--copy` flag (default is stream-copy; `--accurate` re-encodes) and the duplicate `-to`/`--to`.
- `rotate` now works on images too (any angle), not just video.
- Web UI + API op keys renamed to match (`cut`→`trim`, `trim-to-audio`→`match-duration`, `audio-clean`→`clean`, `audio-separate`→`separate`).

### Fixed
- `crop` on video now validates geometry (positive W/H, non-negative offsets) and raises `ValueError`, matching the image path (previously ffmpeg failed late/opaquely).
- README no longer documents the non-existent `video-resize`/`video-convert`/`video-cut` commands.

## 0.11.1

### Fixed
- `convert` with an audio-only output (e.g. `vidtools convert video.mp4 out.mp3`) no longer fails with "Nothing was written into output file". `convert_format` now detects an audio-only output container (via the shared `AUDIO_CODEC_MAP`), drops the video stream with `-vn`, and selects the correct codec (`libmp3lame`, `pcm_s16le`, etc.) instead of forcing `-c:v libx264 -c:a aac` into the audio muxer. Time-range flags are preserved. Fixes both the CLI and the web `convert` dispatch.

## 0.11.0

### Added
- **`screenshot`**: grab a single high-quality still frame from a video at a timestamp. `vidtools screenshot in.mp4 shot.png --at 00:01:30`. Output format is inferred from the extension (`.png`/`.jpg`/`.webp`). PNG is lossless; jpg/webp re-encode via PIL with `--quality` (1-100, higher = better). `--accurate` decodes to the exact frame (slower) instead of the default fast keyframe seek.
- ffmpeg extracts the frame (to a lossless PNG for lossy targets); PIL handles jpg/webp encoding so the quality scale stays consistent and webp works even when ffmpeg is built without libwebp.
- Exposed on the web UI as the `screenshot` op (timestamp/format/quality/accurate), reachable from the mobile-first dropdown.

## 0.10.0

### Added
- **`dl-live`**: record YouTube/HLS live broadcasts that only stream live. Polls until the broadcast starts (`--wait`, default 60s), captures from the beginning (`--live-from-start`), and survives long broadcasts via infinite fragment retries. Output is mp4-only; yt-dlp writes MPEG-TS data into the .mp4 during recording and FixupM3u8 converts to proper mp4 at clean exit, so partial-file playability is preserved either way.
- `--from-now` joins at the live edge instead of recording from the start. `--no-wait` fails immediately if the broadcast isn't live yet.
- Exposed on the web as the `ytdlp-live` op; same flag surface via job params (`wait_interval`, `from_start`).
- `examples/record_live.py`: standalone, zero-deps version of the same recipe (only needs `yt-dlp` on PATH). Useful for understanding which flags matter and why; also documents the macOS `ARG_MAX` issue with `.ts` output and `--hls-use-mpegts`.

### Changed
- `_make_cli_sink` now surfaces text messages via `tqdm.set_postfix_str`. Lets fragment-style live progress show real-time updates ("Recording fragment 142") even though the percent bar can't advance meaningfully for an unbounded stream.

### Known issues (macOS, yt-dlp 2026.x)
- When `download_live()` invokes yt-dlp via `subprocess.Popen` for an HLS-served stream (e.g., YouTube's format 95), yt-dlp's internal ffmpeg invocation aborts with `OSError: Errno 63 File name too long`. The very same `yt-dlp` binary with the very same flag list works when invoked directly from the shell. Trigger has not been isolated. Workaround: for live recordings on macOS, invoke `yt-dlp` directly. See `CLAUDE.md` "Live recording" section.

### Dependencies
- `yt-dlp`: floor raised from `>=2024.0.0` to `>=2026.3.17`. Both versions can hit the `ARG_MAX` bug above; the bump is for general extractor freshness.

## 0.9.0

### Added
- **`trim-to-audio`**: trim a video so its duration matches the audio track plus a configurable buffer. For clips whose video track outlasts the audio (e.g. avatar/TTS-generated videos with trailing silent frames). Probes each stream's duration via ffprobe and applies `-t (audio_duration + buffer)`. Buffer defaults to 0.25s.
- Stream-copies by default (fast, lossless, keyframe-imprecise). `--accurate` re-encodes for frame-accurate trimming with optional `--crf`/`--video-codec`/`--audio-codec`/`--hwaccel`.
- No-op when the audio is already at least as long as the video (file is still remuxed to the output path so callers get a consistent artifact).
- Wired into `_OP_DISPATCH` so the web UI / JobManager can run it as a regular job.
- Not added to the pipeline (requires a probe pass; same constraint as `sanitize` without `--manual-crop`).

- **`audio-clean`**: single-pass vocal cleanup chain. Defaults to the canonical recipe `highpass=80 -> afftdn=nf=-25 -> acompressor(threshold=-15dB,ratio=3,attack=5,release=50) -> loudnorm(I=-14,TP=-1.0,LRA=11)`. Output codec auto-detected from extension; mp3 defaults to libmp3lame VBR `-q:a 2`.
- Each stage is independently skippable via `--no-highpass`, `--no-denoise`, `--no-compress`, `--no-loudnorm`. All parameters individually tunable from CLI; full param surface in the Python API (`vidtools.audio_ops.clean_audio`).
- Wired into `_OP_DISPATCH` for web/job execution.

- **`audio-separate`** (optional extra): audio source separation via `audio-separator`. Mel-band roformer, BS-roformer, Demucs, MDX-Net, VR. CoreML accelerated on Apple silicon via ONNXRuntime. Auto-downloads models on first use. `audio-separate --list` shows the registry. Output dir produces one file per stem.
- Optional extra: install with `uv sync --extra separation` (pulls in torch + onnxruntime + librosa). Core install stays lean.
- `vidtools/separation.py` defines a `SeparationBackend` Protocol; the current `AudioSeparatorBackend` is backend-swappable so a future MLX-native implementation can replace the inference engine without breaking the CLI surface.
- Wired into `_OP_DISPATCH` as `audio-separate`.

## 0.8.1

### Changed (sanitize is now actually sanitizing)
- `sanitize_video` now strips **stream-level** metadata (`-map_metadata:s -1`) in addition to container metadata. The libx264/AAC encoder strings (which the encoders re-add post-encode) are explicitly cleared via `-metadata:s:v:0 encoder=` and `-metadata:s:a:0 encoder=`.
- `-fflags +bitexact` and `-flags:v +bitexact` suppress the libavformat/libavcodec version strings that previously leaked into the output container/stream tags.
- `audio_mode` default changed from `"copy"` to `"aac"`. The original audio bytes (and any audio fingerprint) are no longer preserved by default.
- Cropdetect now uses the full ffmpeg-returned `W:H:X:Y` so top letterbox is also removed, not just bottom (`extra_bottom` still applies to the detected height).
- CLI: `--aac` is gone (now the default). New `--keep-audio` opts back into stream copy. `--no-audio` unchanged. Help text rewritten to honestly describe what the operation does.

## 0.8.0

### Added
- **Operation pipelines**: chain multiple video operations into a single ffmpeg invocation. Faster than running ops sequentially with temp files; no quality loss from multiple re-encode passes. CLI: `vidtools pipeline in.mp4 out.mp4 cut --start 60 --end 90 resize -p 0.5 convert --crf 20`.
- Supported pipeline ops: cut, resize, crop, rotate, sanitize, subtitles, convert. Unsupported (different output type or multiple inputs): concat, slideshow, extract-audio/frames, add-audio, preset.
- `vidtools/pipeline.py`: `Pipeline` class with `build_command()` and `run()`, plus filter builder helpers.
- `_parse_pipeline_steps()` in cli.py: walks argv, splits on op keywords, parses each step with a per-op argparse.

### Validation
- Pipeline rejects `--copy` on convert step (filters require re-encoding).
- Pipeline rejects `sanitize` without `--manual-crop` (probe-based cropdetect not supported in single-pass mode).
- Pipeline rejects duplicate steps (at most one of each op type in v1).
- Smart input detection: pipeline only accepts video inputs (raises clearly for image/audio).

## 0.7.0

### Changed (architectural)
- **SQLite-backed state**: file registry and jobs now persist to `data/vidtools.db` (WAL mode, single connection, threading.Lock).
- File registry is no longer an in-memory dict serialized to JSON. Race conditions during concurrent uploads are eliminated.
- Jobs survive server restarts. On startup, any job left in RUNNING/QUEUED state is marked FAILED with `error="Server restart"`.
- One-time migration from legacy `file_registry.json` to SQLite on first startup. Old file is renamed to `.json.migrated`.

### Added
- `vidtools/db.py`: `Database`, `FileRegistry`, `JobStore`, `migrate_legacy_registry()`.
- `JobManager.load_from_store()` for crash recovery on startup.
- `JobStore.fail_unfinished()` for marking interrupted jobs as failed.

### Removed
- `_save_registry()`, `_load_registry()`, `_register_file()`, `_unregister_file()` from server.py (replaced by FileRegistry methods).
- `REGISTRY_FILE` constant (replaced by `DB_PATH`).

## 0.6.0

### Changed (architectural)
- **Thread-local progress sink** replaces `progress_callback` parameter on every operation. Operations no longer carry presentation concerns through their signatures.
- `run_ffmpeg_command` -> `run_ffmpeg`. Single mode of operation: always raises `FfmpegError` on failure (no more `exit(1)` paths). Progress flows through `vidtools.progress.report_progress()`.
- CLI installs a lazy tqdm sink in `handle_command` via `progress_context()`.
- Web server installs a WebSocket-broadcasting sink in `_run_and_broadcast`. `JobManager.run_job` work_fn signature simplified to `(job)` -- no more `(job, progress_callback)`.
- All 9 `_dispatch_*` functions in server.py drop their `cb` parameter.

### Added
- `vidtools/progress.py`: thread-local progress sink with `set_progress_sink()`, `report_progress()`, `progress_context()`, and `FfmpegError`.
- **Input-type smart dispatch**: `vidtools resize input.png` and `vidtools resize input.mp4` both work. Smart commands: `info`, `crop`, `resize`, `convert`, `cut`. They detect the input type by extension and route to the appropriate operation.

### Fixed
- All 3 `exit(1)` calls in `utils.py` removed -- the library no longer kills its host process.
- Error output is now properly captured (was already fixed in 0.5.0; reaffirmed in the new generator-free design).

### Removed
- `progress_callback` parameter from 73 call sites across operations, server, and CLI.
- Backwards-compatible `run_ffmpeg_command` alias.

## 0.5.0

### Added
- Image operations: resize, convert, crop, thumbnail, strip-exif, optimize, adjust, info
- Audio operations: convert, cut, normalize, mix, fade, resample, silence-detect, silence-remove, info
- CLI commands: img-resize, img-convert, img-crop, img-thumb, img-strip-exif, img-optimize, img-adjust, img-info
- CLI commands: audio-convert, audio-cut, audio-normalize, audio-fade, audio-resample, audio-info
- Hardware acceleration via --hwaccel flag (macOS VideoToolbox) for cut and convert
- HEVC/H.265 and AV1 codec presets (hevc_quality, av1_quality)
- ffmpeg version detection with minimum version check (5.1+)

### Fixed
- Preset application crash: key mismatch between preset dict and convert_format params
- concatenate_videos re-encode mode: `-c libx264` replaced with `-c:v libx264 -c:a aac`
- Error output lost in run_ffmpeg_command: stderr lines now accumulated during progress loop
- Deprecated `-async 1` replaced with `-af aresample=async=1`
- Subtitle path escaping: paths with colons/backslashes now work
- check_ffmpeg_installed returns False on CalledProcessError
- extract_frames validates fps > 0
- Invalid resize_mode in slideshow now raises ValueError

### Changed
- Split operations.py into video_ops.py, image_ops.py, audio_ops.py with facade re-export
- Eliminated main.py: handlers merged inline into cli.py
- Server dispatch refactored from 105-line if/elif to registry dict
- Preset loading uses mtime-based caching
- Codec defaults centralized (DEFAULT_VIDEO_CODEC, DEFAULT_AUDIO_CODEC)

### Removed
- Dead files: tui.py, tui.tcss, batch_convert.py, merge.sh, setup.py, test_sample.py
- Unused ImageFilter import

## 0.4.0

### Changed
- URL downloads now accept any HTTPS URL (not just YouTube) via yt-dlp's broad site support
- Renamed `validate_youtube_url` to `validate_download_url` with HTTPS-only validation
- Switched all remaining `json` usage to `orjson` (presets, operations, CLI info output)
- Web UI "YouTube" tab renamed to "Download" with generic labeling

### Removed
- Dead stub commands: merge, batch (convert/resize/cut/extract-audio), preset save
- Unused progress listener system from JobManager
- Unused `validate_path` function from security module
- Unused `crop_position` parameter from slideshow operation
- Dead re-exports and unused imports

### Added
- `.gitkeep` files for `data/` and `internal/` directory structure
- `_add_faststart_if_mp4` and `_add_time_range_args` helpers in operations.py

## 0.3.0

### Added
- `add-audio` operation: add audio tracks to video files (CLI, web API, and UI)
- Replace mode (default): replaces existing audio with new track
- Mix mode: layers new audio on top of existing audio using amix
- Volume control for the added audio track (0.0-10.0)

## 0.2.0

### Added
- Web-based UI accessible from any browser (mobile-first design)
- FastAPI server with REST API and WebSocket progress updates
- Password authentication with JWT sessions
- YouTube download support via yt-dlp (MP3, WAV, MP4)
- Job queue system with concurrent execution and real-time progress
- File upload/download management with auto-cleanup
- Security layer: bcrypt passwords, input validation, path traversal prevention, rate limiting
- systemd deployment with hardened service config
- Dark/light mode support (follows system preference)
- Setup script for Ubuntu server deployment

### Changed
- `run_ffmpeg_command` now accepts optional `progress_callback` for non-CLI usage
- Bumped Python requirement from 3.8 to 3.12
- Removed emojis from error output in utils.py

### Dependencies
- Added: fastapi, uvicorn, python-multipart, python-jose, bcrypt, yt-dlp, orjson

## 0.1.0

### Added
- Initial CLI with 14 FFmpeg operations
- Preset management system
- Optional TUI via Textual
- Structured logging with structlog
- Progress bars with tqdm
