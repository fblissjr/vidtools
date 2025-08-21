#!/usr/bin/env python
"""
Simple test script to verify vidtools package is working.
"""

import vidtools

print(f"vidtools version: {vidtools.__version__}")
print(f"Available functions: {[f for f in dir(vidtools) if not f.startswith('_')]}")

# List available presets
print("\nAvailable presets:")
presets = vidtools.get_presets()
for name, preset in presets.items():
    print(f"  - {name}: {preset.get('description', 'No description')}")

print("\nPackage successfully installed and working!")
print("\nYou can now use:")
print("  vidtools <command> --help")
print("  vt <command> --help")
print("\nExample:")
print("  vt cut input.mp4 output.mp4 --start 10 --duration 30")