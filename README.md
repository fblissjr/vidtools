# vidtools

Media tools CLI. Built because I always forget ffmpeg flags.

Handles video (FFmpeg), images (PIL), and audio (FFmpeg).

## Quick start

Requires Python 3.12+. Video and audio verbs need ffmpeg >= 5.1 and ffprobe in
PATH; yt-dlp in PATH for URL downloads. The image verbs are pure PIL and run on
a machine with no ffmpeg at all — the toolchain check is scoped to the
invocations that actually need it.

**On macOS, `brew install ffmpeg` is not enough for every verb.** Homebrew's
`ffmpeg` formula carries ten dependencies and omits libass, libaom-av1 and
libvorbis. That means `subtitles` burn-in is unavailable (it reports this
clearly rather than failing cryptically), and the `av1_quality` preset and
`.ogg` output will fail. `brew install ffmpeg-full` is the batteries-included
build. WebP is unaffected — images encode through PIL, not ffmpeg.

```bash
uv sync
uv run vidtools <command> [options]
```

## CLI

One verb per concept. Shape: `vidtools <operation> [params] [--format] [power-user flags] <input> <output>`.

The common verbs auto-detect the input type from its extension and do the right thing — you don't pick audio vs video vs image. `vidtools resize photo.png` uses PIL; `vidtools resize clip.mp4` uses ffmpeg; same command.

### Universal commands (auto-detect input type)

```bash
vidtools info file.{mp4,png,mp3}                       # video, image, or audio info
vidtools convert in.avi out.mp4                        # video convert
vidtools convert in.wav out.mp3                         # audio convert
vidtools convert clip.mp4 audio.mp3                     # video -> extract audio
vidtools convert photo.png photo.webp -q 90            # image convert (quality 1-100)
vidtools resize clip.mp4 out.mp4 -p 0.5                # video or image
vidtools resize photo.png small.png -W 800
vidtools crop in.png out.png -W 200 -H 200 -x 50 -y 50 # video or image
vidtools trim --start 60 --end 90 in.mp4 out.mp4       # video or audio (alias: cut)
vidtools trim --start 0 --end 30 song.mp3 clip.mp3
vidtools rotate -r 90 clip.mp4 out.mp4                 # video: 90/180/270/-90
vidtools rotate -r 45 photo.png out.png               # image: any angle
```

`--quality` is a unified 1-100 knob (higher = better) on `convert`; power users can still pass `--crf` (video) or `--bitrate` (audio) to override. Video-specific power-user flags (`--algorithm`, `--video-codec`, `--accurate`, `--hwaccel`, ...) are accepted by the same verbs when the input is a video.

### Video-only commands

```bash
vidtools convert in.mp4 out.gif                       # GIF with palette generation
vidtools extract-audio in.mp4 out.mp3
vidtools extract-frames in.mp4 'frame_%04d.jpg' -r 1
vidtools screenshot --at 00:01:30 in.mp4 shot.png     # single high-quality frame (png/jpg/webp)
vidtools concat a.mp4 b.mp4 c.mp4 -o combined.mp4
vidtools subtitles -s subs.srt in.mp4 out.mp4
vidtools sanitize in.mp4 out.mp4                      # auto-crop, noise, strip metadata
vidtools slideshow --size 1920x1080 --resize-mode pad 'images/*.png' out.mp4
vidtools add-audio --mix --volume 0.3 video.mp4 music.mp3 out.mp4
vidtools match-duration --buffer 0.5 in.mp4 out.mp4   # trim video to its audio length + buffer
vidtools srt-from-transcript transcript.txt out.srt
```

### Image-only commands (PIL)

```bash
vidtools thumbnail -s 256 in.png thumb.png
vidtools strip-metadata in.jpg clean.jpg
vidtools optimize -q 80 --max-width 1920 in.png small.jpg
vidtools adjust --brightness 1.2 --contrast 1.1 --rotation 90 --flip horizontal in.png out.png
vidtools adjust --rotation 45 --fill white in.png out.png   # corner fill for off-axis rotation
```

`--fill` sets the colour of the corners a non-right-angle rotation exposes
(colour name or `#rrggbb`; transparent by default).

(For image resize/convert/crop/rotate/info, use the universal verbs above.)

#### compress — shrink an image without guessing

`optimize` recompresses into the format you name. `compress` picks the format
*and* the quality by measuring: it encodes candidates, scores each against the
original with SSIM, and writes the smallest one that clears a fidelity target.

```bash
vidtools compress photo.jpg                          # -> photo_compressed.<winning ext>
vidtools compress photo.jpg small                    # winner supplies the extension
vidtools compress photo.jpg out.webp                 # or name the format yourself
vidtools compress photo.jpg --dry-run                # rank candidates, write nothing
vidtools compress photo.jpg --target-ssim 0.99       # fidelity target 0-1 (default 0.98)
vidtools compress photo.jpg --max-size 500k          # byte budget; maximises fidelity within it
vidtools compress logo.png --lossless                # exact encodes only, smallest wins
vidtools compress photo.jpg --allow-avif             # opt into AVIF
vidtools compress photo.jpg --fit vision             # downscale for a vision API first
```

`--fit` downscales to what the destination will actually keep, before
compressing. Sending an image past a vision API's ceiling means it resamples on
arrival — you paid to compress detail that gets thrown away, and took a second
resampling pass in quality. `vision` targets Claude's high-resolution tier,
`vision-standard` the smaller one. It never upscales, and it solves in patch
space rather than pixel area, because a partial 28x28 patch costs a whole token.

Output never overwrites a previous run when the name is derived: a second
`vidtools compress photo.jpg` writes `photo_compressed-2.<ext>` rather than
replacing the first result. An output you name explicitly is overwritten as
usual — auto-renaming what you asked for would break scripts.

Defaults to PNG/JPEG/WebP — the formats the Claude and Gemini vision APIs
accept. AVIF usually compresses far better but no major vision API decodes it,
so it is opt-in (naming a `.avif` output implies `--allow-avif`). Text and UI
screenshots are detected and held to a stricter target, since SSIM barely
registers the ringing that makes glyphs mushy.

Pillow-only — no `cwebp`/`avifenc` subprocesses. A build without AVIF or WebP
degrades to the formats it has rather than failing.

### Audio commands (FFmpeg)

These also accept **video** inputs: an audio output extracts and processes the
audio; a video output processes the audio track and keeps the picture.

```bash
vidtools normalize --target -16 in.mp3 out.mp3        # LUFS, two-pass loudnorm
vidtools normalize clip.mp4 out.mp4                   # video kept, audio normalized
vidtools fade --fade-in 3 --fade-out 3 --total-duration 60 in.mp3 out.mp3
vidtools resample -r 22050 in.mp3 out.mp3
vidtools clean in.mp3 out.mp3                          # highpass + denoise + compress + loudnorm
vidtools clean lecture.mp4 cleaned.mp4                # clean the talk's audio, keep video
```

(For audio convert/trim/info, use the universal verbs above.)

### Audio source separation (optional)

Splits a track into stems (vocals/instrumental, or 4-stem) using mel-band roformer, BS-roformer, Demucs, MDX-Net, or VR via [`audio-separator`](https://github.com/nomadkaraoke/python-audio-separator). CoreML accelerated on Apple silicon.

```bash
uv sync --extra separation              # one-time install of torch + onnxruntime + librosa

vidtools separate --list                                                         # list available models
vidtools separate song.mp3 mel_band_roformer_kim_FT_by_aname-tommy.ckpt ./stems/
vidtools separate song.mp3 htdemucs_ft.yaml ./stems/                             # 4-stem
```

Models auto-download to the audio-separator cache on first use. The backend is swappable (`vidtools.separation.SeparationBackend` Protocol) so a future MLX-native implementation can replace the inference engine without changing the CLI.

### Pipelines (chain video ops in one ffmpeg invocation)

```bash
vidtools pipeline in.mp4 out.mp4 \
    trim --start 60 --end 90 \
    resize -p 0.5 \
    convert --crf 20
```

3-5x faster than running separate commands; no intermediate files; no compounding quality loss. Supports trim (alias cut), resize, crop, rotate, sanitize, subtitles, convert. Excludes multi-input or different-output ops (concat, slideshow, extract-*, add-audio, preset).

### Metadata: what gets stripped

**Identifying metadata is removed by default.** GPS coordinates, camera
make/model and capture timestamps do not survive into output. Pass
`--preserve-metadata` to keep them.

Rendering data is always preserved: EXIF orientation (baked into the pixels on
open, so the output is upright rather than tagged) and the ICC colour profile.
Dropping those would change how the image looks, which is a pixel change in all
but name.

```bash
vidtools resize phone-photo.jpg small.jpg                      # GPS gone
vidtools resize phone-photo.jpg small.jpg --preserve-metadata  # GPS kept
```

The original file is untouched either way — these verbs write a new file. That
asymmetry is the reason for the default: the original keeps everything, while a
location you published by accident does not come back.

Granularity differs by medium. For **images** the kept set is an allowlist
(copyright, artist, description), so `--preserve-metadata` restores authorship
without re-admitting GPS. For **video**, ffmpeg's metadata mapping is
all-or-nothing at the container level, so the flag keeps everything, location
included.

Verbs that take `--preserve-metadata`: `convert`, `resize`, `crop`, `trim`,
`rotate`, `concat`, `add-audio`, `match-duration`, `thumbnail`, `optimize`,
`adjust`, `pipeline`, `subtitles`, `normalize`, `fade`, `resample`, `clean`,
`extract-audio`, `extract-frames`, `slideshow`.
`sanitize` and `strip-metadata` always strip — that is their job.

**Audio inverts the default, on purpose.** For a photo or a clip, metadata is
incidental. For audio it is frequently the point: artist, title and album are
what make a library work, and `convert song.mp3 song.m4a` handing back an
untitled file would be a worse bug than the leak. So audio **keeps** the
descriptive tags (artist, title, album, track, genre, date) and **drops** the
free-form and recorder ones (`comment`, `description`, `encoded_by`, `location`,
`make`, `model`). `comment` is the one that matters — it is where an entire
generation prompt turned up in the video case.

`extract-audio` takes the **audio** policy rather than the video one, because
its output is an audio file: title and artist survive, location and comment are
blanked. `extract-frames` and `slideshow` strip.

### Presets

```bash
vidtools preset list
vidtools preset apply in.mp4 out.mp4 compress_web
```

Built-in presets include `compress_web`, `audio_only_mp3`, `gif_optimized`, `resize_half`, `hq_h264_mp4`, `mobile_friendly_mp4`, `webm_social_media`, `hevc_quality` (libx265), `av1_quality` (libaom-av1).

### Hardware acceleration

```bash
vidtools convert in.mp4 out.mp4 --hwaccel        # macOS: uses VideoToolbox
vidtools cut in.mp4 out.mp4 --start 0 --end 60 --accurate --hwaccel
```

## Finder Quick Action (macOS)

Right-click images in Finder, pick a result, get compressed copies alongside
the originals.

```bash
bash macos/install.sh              # install
bash macos/install.sh --uninstall  # remove
```

One menu entry that opens a preset list, rather than one entry per operation --
the convention macOS's own Convert Image follows, and it keeps the Quick Actions
menu from filling up. Presets are outcome-shaped: *Smallest, sized for AI
vision*, *Balanced*, *Visually lossless*, *Lossless*, *Balanced but keep
metadata*.

The bundle is generated at install time rather than checked in: the embedded
script needs your machine's absolute path to the `vidtools` binary, and a
`.wflow` is an opaque plist. The reviewable part -- `macos/compress-image.sh` --
stays in plain sight, and `macos/make_workflow.py` builds the plist so it can be
tested.

**Python changes reach the action immediately** (the venv install is editable),
but the shell script is embedded into the bundle at install time -- editing
`macos/compress-image.sh` needs a re-run of `install.sh` to take effect. Moving
the repo also breaks the action, which fails with a visible alert rather than
doing nothing silently.

Automator rather than a native app, so there is no code-signing or notarisation
step. The trade is that AppleScript cannot draw a multi-control panel, so it is
one preset list instead of the Format / Size / Preserve Metadata trio. If that
becomes limiting, `macos/compress-image.sh` is what a native front end would
call.

## Architecture

Operations are split by media type and re-exported from a thin facade:

- `video_ops.py` -- FFmpeg video operations + shared filter helpers
- `image_ops.py` -- PIL image operations
- `audio_ops.py` -- FFmpeg audio operations
- `compress.py` -- measured format/quality search (see `compress` above)
- `operations.py` -- facade (`from .video_ops import *` etc.)

Cross-cutting infrastructure:

- `progress.py` -- thread-local progress sink. Operations call `report_progress(percent, msg)`; the CLI installs a tqdm sink. No callback parameters in operation signatures.
- `pipeline.py` -- `Pipeline` class chains video ops into a single ffmpeg invocation with a combined `-vf` filter chain.
- `cli.py` -- argparse + smart input-type dispatch (`_make_smart`, `_detect_kind`).
- `ytdlp.py` -- yt-dlp subprocess wrapper.

## Project structure

```
vidtools/
  __init__.py
  _version.py
  cli.py             # argparse + smart dispatch + pipeline argv parser
  operations.py      # facade re-exporting from video_ops/image_ops/audio_ops
  video_ops.py       # FFmpeg video ops + shared filter helpers
  image_ops.py       # PIL image ops
  audio_ops.py       # FFmpeg audio ops
  compress.py        # measured format/quality search for images
  utils.py           # run_ffmpeg, ffmpeg version check, file validation
  progress.py        # thread-local progress sink + FfmpegError
  pipeline.py        # Pipeline chaining (single ffmpeg invocation)
  ytdlp.py           # yt-dlp wrapper
  presets.py         # Preset management with mtime cache
data/                # Output and temp files -- gitignored except .gitkeep
tests/               # run: uv run pytest tests/ -v
```

## Tests

```bash
uv run pytest tests/ -v
```
