# FFmpeg Video Compression & Metadata Removal Recipes (macOS Apple Silicon / M2 Ultra)

These recipes compress video files to fit under a strict size limit (default: **< 100 MB / 100,000,000 bytes**) while completely stripping container, stream, chapter, and bitstream encoder metadata.

## Built-in `vidtools compress` & Finder Quick Action Support

Recipes 1–5 are built directly into `vidtools compress` and the macOS **Compress Video** Finder Quick Action (`macos/install.sh`):

```zsh
vidtools compress input.mp4                                  # Recipe 1: universal (<100 MB H.264 + aac_at)
vidtools compress input.mp4 --recipe hevc                    # Recipe 2: hevc (<100 MB 10-bit HEVC + aac_at)
vidtools compress input.mp4 --recipe hw                      # Recipe 3: hw (<100 MB VideoToolbox HEVC + aac_at)
vidtools compress input.mp4 --recipe archival                # Recipe 4: archival (10-bit HEVC CRF 16 + 320k aac_at)
vidtools compress input.mp4 --recipe copy                    # Recipe 5: copy (stream copy + strip metadata)
vidtools compress input.mp4 --recipe balanced-1080p          # 1080p max-edge cap, H.264 CRF 23
vidtools compress input.mp4 --recipe small-720p              # 720p max-edge cap, H.264 CRF 26
vidtools compress input.mp4 --recipe smallest-480p           # 480p max-edge cap, H.264 CRF 28
vidtools compress input.mp4 --recipe crf                     # Original dimensions, H.264 CRF 23
```

Pass `--preserve-metadata` on the CLI (or check **Keep source metadata** in the Finder Quick Action dialog) to retain container/stream metadata and chapters.

---

## How to Calculate Target Video Bitrate (`-b:v`)

To fit safely under **100,000,000 bytes (100 MB decimal / ~95.37 MiB)** with headroom for MP4 container overhead, aim for **95,000,000 bytes (`760,000,000` bits)**:

```zsh
# Get duration in seconds:
DURATION=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "input.mp4")

# Target video bitrate in kbps (reserving 192 kbps for AAC audio):
# Bitrate (kbps) = (760000 / DURATION) - 192
python3 -c "d = float('$DURATION'); print(f'{int((760000 / d) - 192)}k')"
```

*(Example for a `266.46s` video: `(760000 / 266.46) - 192` ≈ **`2650k`**)*

---

## Recipe 1: Sharing / Universal Compatibility (2-Pass H.264 + Apple AAC)

Best for sharing
- Uses `libx264` 2-pass (`-preset veryslow`) for maximum quality per bit and strict bitrate adherence.
- Uses `-bsf:v filter_units=remove_types=6` on Pass 2 to strip `libx264`'s internal SEI bitstream info header (NAL unit type 6).
- Uses macOS AudioToolbox AAC (`aac_at`) at `192k`.
- Strips all container, stream, and chapter metadata (`-map_metadata -1 -map_chapters -1`) and suppresses `Lavf`/`Lavc` tags (`+bitexact`).

```zsh
INPUT="input.mp4"
OUTPUT="output_share.mp4"
VBITRATE="2650k"

ffmpeg -y -i "$INPUT" \
  -c:v libx264 -preset veryslow -b:v "$VBITRATE" \
  -pass 1 -an -f null /dev/null && \
ffmpeg -y -i "$INPUT" \
  -map 0:v:0 -map 0:a:0 \
  -map_metadata -1 -map_chapters -1 \
  -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
  -c:v libx264 -preset veryslow -b:v "$VBITRATE" \
  -pass 2 -pix_fmt yuv420p \
  -bsf:v filter_units=remove_types=6 \
  -c:a aac_at -b:a 192k \
  -movflags +faststart \
  "$OUTPUT" && \
rm -f ffmpeg2pass-0.log ffmpeg2pass-0.log.mbtree
```

---

## Recipe 2: Maximum Visual Quality per MB (2-Pass 10-bit HEVC / H.265)

Best when the target device/player supports **HEVC / H.265** (macOS, iOS, modern desktop players).
- Achieves ~30–40% better compression efficiency than H.264.
- Uses 10-bit pixel format (`yuv420p10le`) to eliminate gradient banding.
- Uses `-tag:v hvc1` for native Apple QuickTime / iOS / macOS compatibility.
- Uses `no-info=1` in `-x265-params` to prevent x265 SEI bitstream metadata.

```zsh
INPUT="input.mp4"
OUTPUT="output_hevc.mp4"
VBITRATE="2650k"

ffmpeg -y -i "$INPUT" \
  -c:v libx265 -preset slow -b:v "$VBITRATE" \
  -pix_fmt yuv420p10le -tag:v hvc1 \
  -x265-params pass=1:no-info=1 -an -f null /dev/null && \
ffmpeg -y -i "$INPUT" \
  -map 0:v:0 -map 0:a:0 \
  -map_metadata -1 -map_metadata:s -1 -map_chapters -1 \
  -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
  -metadata:s:v:0 encoder= -metadata:s:a:0 encoder= \
  -c:v libx265 -preset slow -b:v "$VBITRATE" \
  -x265-params pass=2:no-info=1 \
  -pix_fmt yuv420p10le -tag:v hvc1 \
  -c:a aac_at -b:a 192k \
  -movflags +faststart \
  "$OUTPUT" && \
rm -f x265_2pass.log x265_2pass.log.cutree
```

---

## Recipe 3: Fast Hardware Encoding on M2 Ultra (`hevc_videotoolbox` / `h264_videotoolbox`)

Best when speed matters most (uses the M2 Ultra Media Engine to finish in seconds).
- Hardware encoders only run 1-pass ABR, so lower the video bitrate slightly (e.g., `2500k` instead of `2650k`) for extra file-size safety margin.

```zsh
INPUT="input.mp4"
OUTPUT="output_hw.mp4"
VBITRATE="2500k"

# Hardware HEVC (use -c:v h264_videotoolbox without -tag:v hvc1 for H.264)
ffmpeg -y -i "$INPUT" \
  -map 0:v:0 -map 0:a:0 \
  -map_metadata -1 -map_chapters -1 \
  -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
  -c:v hevc_videotoolbox -b:v "$VBITRATE" -tag:v hvc1 \
  -c:a aac_at -b:a 192k \
  -movflags +faststart \
  "$OUTPUT"
```

---

## Recipe 4: Visually Lossless Archival Compression (No Size Cap)

When you want the highest possible visual and audio quality while still compressing considerably smaller than raw/high-bitrate captures (and stripping all metadata):
- Uses 10-bit HEVC (`libx265 -crf 16 -preset slow -pix_fmt yuv420p10le -tag:v hvc1`).
- Uses `aq-mode=3` (auto-variance adaptive quantization with dark-scene bias) and `no-info=1` (strips x265 SEI header).
- Uses `aac_at -b:a 320k` (or `-c:a alac` for bit-exact lossless audio).

```zsh
INPUT="input.mp4"
OUTPUT="output_hq.mp4"

ffmpeg -y -i "$INPUT" \
  -map 0:v:0 -map 0:a:0 \
  -map_metadata -1 -map_chapters -1 \
  -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
  -c:v libx265 -crf 16 -preset slow \
  -pix_fmt yuv420p10le -tag:v hvc1 \
  -x265-params no-info=1:aq-mode=3 \
  -c:a aac_at -b:a 320k \
  -movflags +faststart \
  "$OUTPUT"
```

---

## Recipe 5: Lossless Stream Copy + Strip All Metadata Only (No Re-encoding)

When a file is already small enough and you only want to strip metadata and optimize for web streaming:

```zsh
ffmpeg -y -i "input.mp4" \
  -map 0:v:0 -map 0:a:0? \
  -map_metadata -1 -map_chapters -1 \
  -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
  -c copy \
  -movflags +faststart \
  "output_clean.mp4"
```

---

## Recipe 6: Insert a Custom Banner / Poster Image as Frame 0 (Sharing Thumbnail)

Many chat and sharing apps ignore MP4 `attached_pic` tags and always grab **Frame 0 (`t = 0.00s`)** for the video preview.
To stamp a custom image (`banner.jpg`) onto Frame 0 (letterboxed cleanly to the video's dimensions without stretching or cropping text):

```zsh
INPUT="input.mp4"
BANNER="banner.jpg"
OUTPUT="output_with_banner.mp4"
WIDTH=1424
HEIGHT=1080

ffmpeg -y -i "$INPUT" -i "$BANNER" \
  -filter_complex "[1:v]scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[b];[0:v][b]overlay=0:0:enable='eq(n,0)',format=yuv420p[v]" \
  -map "[v]" -map 0:a:0 \
  -map_metadata -1 -map_chapters -1 \
  -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
  -c:v libx264 -preset veryslow -b:v 2650k \
  -bsf:v filter_units=remove_types=6 \
  -c:a aac_at -b:a 192k \
  -movflags +faststart \
  "$OUTPUT"
```
