import json
import os
from typing import Dict, Any
from utils import logger  # Import logger from utils

# --- Configuration and Presets ---
PRESET_FOLDER = "presets"  # Define the folder name
PRESET_FILE = os.path.join(PRESET_FOLDER, "ffmpeg_presets.json") # Construct path to preset file

DEFAULT_PRESETS = {
    "compress_web": {
        "format": "mp4",
        "vcodec": "libx264",
        "acodec": "aac",
        "quality": "28",
        "vbitrate": None,
        "abitrate": "128k",
        "description": "Compress video for web sharing (good balance of size and quality)."
    },
    "audio_only_mp3": {
        "format": "mp3",
        "vcodec": None,
        "acodec": "libmp3lame",
        "vbitrate": None,
        "abitrate": "192k",
        "description": "Extract audio to MP3 format (high quality)."
    },
    "gif_optimized": {
        "format": "gif",
        "vcodec": None,
        "acodec": None,
        "description": "Create optimized GIF (better quality, smaller size, may be slower)."
    },
    "resize_half": {
        "resize_percentage": 0.5,
        "description": "Resize video to 50% of original resolution."
    },
    "hq_h264_mp4": {
        "format": "mp4",
        "vcodec": "libx264",
        "acodec": "aac",
        "quality": "22",
        "vbitrate": None,
        "abitrate": "192k",
        "description": "High quality H.264 MP4 (larger file size)."
    },
    "mobile_friendly_mp4": {
        "format": "mp4",
        "vcodec": "libx264",
        "acodec": "aac",
        "quality": "32",
        "vbitrate": None,
        "abitrate": "96k",
        "description": "Mobile-friendly MP4 (smaller file size, decent quality)."
    },
    "webm_social_media": {
        "format": "webm",
        "vcodec": "libvpx-vp9",
        "acodec": "libopus",
        "quality": "30",
        "vbitrate": None,
        "abitrate": "128k",
        "description": "WebM for social sharing, YouTube)."
    },
}

def load_presets() -> Dict[str, Dict[str, Any]]:
    """Loads presets from JSON file or uses defaults if file not found."""
    try:
        # Ensure the presets folder exists, create if not
        if not os.path.exists(PRESET_FOLDER):
            os.makedirs(PRESET_FOLDER)

        with open(PRESET_FILE, "r") as f:
            presets = json.load(f)
            merged_presets = {**DEFAULT_PRESETS, **presets}  # Merge loaded with defaults
            logger.debug("Presets loaded from file", file=PRESET_FILE, loaded_count=len(presets), merged_count=len(merged_presets)) # Logging preset load
            return merged_presets
    except FileNotFoundError:
        logger.warning("Preset file not found, using default presets.", file=PRESET_FILE) # Logging file not found
        return DEFAULT_PRESETS
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in preset file, using default presets.", file=PRESET_FILE, error=str(e)) # Logging JSON error
        return DEFAULT_PRESETS

def save_presets(presets: Dict[str, Dict[str, Any]]):
    """Saves presets to JSON file."""
    try:
        # Ensure the presets folder exists before saving
        if not os.path.exists(PRESET_FOLDER):
            os.makedirs(PRESET_FOLDER)

        with open(PRESET_FILE, "w") as f:
            json.dump(presets, f, indent=4)
        logger.info("Presets saved to file", file=PRESET_FILE, preset_count=len(presets)) # Logging preset save
    except Exception as e:
        logger.error("Error saving presets to file", file=PRESET_FILE, error=str(e), exc_info=True) # Logging save error

# ... (rest of presets.py - no changes needed)
def get_presets() -> Dict[str, Dict[str, Any]]:
    """Returns the currently loaded presets."""
    return load_presets() # Presets are loaded every time they are needed to reflect file changes. Consider caching if performance becomes an issue.

def save_preset_command(preset_name: str, preset_data: Dict[str, Any]) -> bool:
    """Saves a preset. Handles overwriting and updates JSON file."""
    presets = get_presets()
    if preset_name in presets:
        overwrite = input(f"Preset '{preset_name}' already exists. Overwrite? (y/n): ").lower()
        if overwrite != 'y':
            logger.info("Preset saving cancelled by user", preset_name=preset_name) # Logging cancellation
            print("Preset saving cancelled.")
            return False
    presets[preset_name] = preset_data
    save_presets(presets)
    logger.info("Preset saved", preset_name=preset_name) # Logging successful save
    print(f"Preset '{preset_name}' saved successfully.")
    return True

def delete_preset(preset_name: str) -> bool:
    """Deletes a preset and updates JSON file."""
    presets = get_presets()
    if preset_name not in presets:
        logger.warning("Attempted to delete non-existent preset", preset_name=preset_name) # Logging non-existent preset delete
        print(f"Error: Preset '{preset_name}' not found.")
        return False

    del presets[preset_name]
    save_presets(presets)
    logger.info("Preset deleted", preset_name=preset_name) # Logging successful delete
    print(f"Preset '{preset_name}' deleted successfully.")
    return True

def edit_preset_command(preset_name: str) -> bool:
    """Opens the preset JSON file in the default text editor for manual editing."""
    presets = get_presets()
    if preset_name not in presets:
        logger.warning("Attempted to edit non-existent preset", preset_name=preset_name) # Logging non-existent preset edit
        print(f"Error: Preset '{preset_name}' not found. Cannot edit.")
        return False

    print(f"Opening preset file '{PRESET_FILE}' in your default text editor.")
    print("Please edit the JSON file and save it. Rerun the script to load changes.")

    try:
        import sys, os, subprocess
        if sys.platform.startswith('win'):
            os.startfile(PRESET_FILE)
        elif sys.platform.startswith('linux'):
            subprocess.run(['xdg-open', PRESET_FILE], check=False)
        elif sys.platform.startswith('darwin'):
            subprocess.run(['open', PRESET_FILE], check=False)
        else:
            print("Could not automatically open the preset file. Please open it manually: ", PRESET_FILE)
            logger.warning("Could not automatically open preset file", file=PRESET_FILE, platform=sys.platform) # Logging open failure
            return False
        logger.info("Preset file opened for editing", file=PRESET_FILE) # Logging file open success
        return True
    except Exception as e:
        logger.error("Error opening preset file for editing", file=PRESET_FILE, error=str(e), exc_info=True) # Logging open error
        print(f"Error opening preset file: {e}")
        print("Please open it manually: ", PRESET_FILE)
        return False

def list_presets():
    """Lists available presets and their descriptions."""
    presets = get_presets()
    print("Available Presets:")
    for name, preset in presets.items():
        description = preset.get("description", "No description provided.")
        print(f"  - {name}: {description}")
    logger.info("Listed available presets", count=len(presets)) # Logging preset listing