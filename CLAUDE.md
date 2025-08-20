# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Code and Writing Style Guidelines

- **No emojis** in code, display names, or documentation
- Keep all naming and display text professional
- Avoid "Enhanced", "Advanced", "Ultimate" type prefixes - use descriptive names instead
- Clean, simple names that describe what they do

## Working with 3rd Party Libraries

- Ensure you search for the latest llms.txt or Python docs for the library you are using or proposing
- If the latest docs are not available, search for the latest version of the library on the official website or GitHub repository
- Prioritize the latest code and docs over your own training data
- Use the latest version of the library over older versions
- Prioritize libraries that are actively maintained and have a large community

## FFmpeg Usage

Ensure you always work with the latest ffmpeg documentation and features.

## Project Overview

This is a Python-based FFmpeg wrapper tool that provides a simplified interface for common video processing tasks. The project consists of a CLI interface, an optional TUI (Text User Interface), and a preset management system.

## Architecture

### Core Components

- **main.py**: Central orchestrator containing all FFmpeg command handlers and business logic. Functions follow a pattern of `<operation>_handler()` for CLI dispatch and core `<operation>()` functions for actual implementation.

- **cli.py**: Argument parser setup using argparse. Defines all available commands (resize, sanitize, merge, etc.) and their parameters. Entry point for command-line usage.

- **utils.py**: Utility functions including FFmpeg command execution with progress bars, logging setup (using structlog), and file validation helpers.

- **presets.py**: Preset management system for saving and loading FFmpeg configurations. Stores presets in `presets/ffmpeg_presets.json`.

- **tui.py**: Optional Textual-based TUI for interactive usage. Provides a visual interface for all operations.

### Key Patterns

1. **Command Flow**: CLI → Handler Function → Core Function → FFmpeg Execution
2. **Error Handling**: Comprehensive error checking with informative messages, logging via structlog
3. **Progress Tracking**: Real-time progress bars for FFmpeg operations using tqdm
4. **Preset System**: JSON-based preset storage with default presets and user customization

## Common Development Tasks

### Running the CLI

```bash
# Basic usage
python main.py <command> [options]

# Examples
# Cut video from 40s to 1:06.5 (fast, no re-encoding)
python main.py cut input.mp4 output.mp4 --start 00:00:40 --end 00:01:06.500

# Cut with frame accuracy (slower, re-encodes)
python main.py cut input.mp4 output.mp4 --start 40 --duration 26.5 --accurate

# Resize to 50% 
python main.py resize input.mp4 output.mp4 -p 0.5

# Convert to MP4 (auto-detects format from extension)
python main.py convert input.avi output.mp4

# Convert to optimized GIF
python main.py convert input.mp4 output.gif

# Extract audio only
python main.py extract-audio input.mp4 output.mp3 --format mp3

# Concatenate videos with same codecs
python main.py concat video1.mp4 video2.mp4 video3.mp4 -o combined.mp4

# Crop video
python main.py crop input.mp4 output.mp4 -w 640 -h 480 -x 100 -y 50

# Apply preset
python main.py preset apply input.mp4 output.mp4 compress_web
```

### Running the TUI

```bash
python tui.py
```

### Available Commands

- **cut**: Cut/trim video with precise timestamps (supports -c copy for speed)
- **resize**: Scale video by percentage or to specific dimensions
- **convert**: Convert between video formats with smart encoding
- **extract-audio**: Extract audio track from video
- **extract-frames**: Extract frames as images
- **concat**: Join multiple videos (same codec required for -c copy)
- **crop**: Crop video to specified dimensions
- **rotate**: Rotate video (90, 180, 270, -90 degrees)
- **subtitles**: Burn subtitles into video
- **sanitize**: Auto-crop banners, add noise, strip metadata
- **merge**: Merge clips with transitions (placeholder, not implemented)
- **info**: Display video information using ffprobe
- **preset**: Manage and apply presets (apply, list, save, delete, edit)

### Adding New Operations

1. Add command definition in `cli.py:setup_argparse()`
2. Create handler function in `main.py` following pattern: `<operation>_handler(args)`
3. Implement core function in `main.py` with actual FFmpeg logic
4. If needed, add TUI support in `tui.py:action_set_operation()`

## Dependencies

- Python 3.x
- ffmpeg and ffprobe (must be in PATH)
- Python packages:
  - tqdm (progress bars)
  - structlog (structured logging)
  - textual (for TUI, optional)

## FFmpeg Best Practices Implemented

1. **Stream Copy (-c copy)**: Used by default for cut/concat operations to avoid quality loss and maximize speed
2. **Fast Seek**: Option to place -ss before input for faster (but less accurate) seeking
3. **Frame-Accurate Cuts**: --accurate flag for precise cuts with re-encoding
4. **Smart Time Handling**: Supports both -to (absolute) and -t (duration) with multiple time formats
5. **Audio Sync Fixes**: --fix-sync option adds -async 1 for problematic files
6. **Fast Start**: Automatically adds -movflags +faststart for MP4 files
7. **Aspect Ratio Preservation**: Uses -2 instead of -1 for even dimensions in resize
8. **Optimized GIF Creation**: Uses palette generation for better quality GIFs
9. **Codec Auto-Detection**: Convert command auto-detects format from file extension

## Important Notes

- All file paths in the code use absolute paths
- FFmpeg commands are executed via subprocess with proper error handling
- The merge command is currently a stub and not implemented
- Preset modifications are immediately saved to disk
- Use -c copy (stream copy) whenever possible for speed and quality preservation
- Re-encode only when necessary (different codecs, frame-accurate cuts, filters)