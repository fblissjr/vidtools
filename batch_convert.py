#!/usr/bin/env python
"""
Batch convert files using vidtools.
"""

import os
import sys
import glob
import argparse
from pathlib import Path
import subprocess

def batch_convert(pattern, output_format, output_dir=None):
    """
    Batch convert files matching pattern to output format.
    
    Args:
        pattern: File pattern (e.g., "*.webp", "images/*.png")
        output_format: Output format (e.g., "mp4", "gif")
        output_dir: Optional output directory (default: same as source)
    """
    files = glob.glob(pattern)
    
    if not files:
        print(f"No files found matching pattern: {pattern}")
        return
    
    print(f"Found {len(files)} files to convert")
    
    for input_file in files:
        input_path = Path(input_file)
        
        # Determine output path
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            output_file = Path(output_dir) / f"{input_path.stem}.{output_format}"
        else:
            output_file = input_path.with_suffix(f".{output_format}")
        
        print(f"Converting: {input_file} -> {output_file}")
        
        # Use vidtools for conversion
        cmd = ["vt", "convert", str(input_file), str(output_file)]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"  ✓ Success")
            else:
                print(f"  ✗ Failed: {result.stderr}")
        except Exception as e:
            print(f"  ✗ Error: {e}")
    
    print(f"\nBatch conversion complete!")

def main():
    parser = argparse.ArgumentParser(description="Batch convert media files")
    parser.add_argument("pattern", help="File pattern (e.g., '*.webp', 'images/*.png')")
    parser.add_argument("format", help="Output format (e.g., mp4, gif, jpg)")
    parser.add_argument("-o", "--output-dir", help="Output directory (default: same as source)")
    parser.add_argument("--fps", type=int, default=30, help="Frame rate for image sequences (default: 30)")
    parser.add_argument("--duration", type=float, default=1.0, help="Duration per image for slideshows (default: 1.0)")
    
    args = parser.parse_args()
    
    batch_convert(args.pattern, args.format, args.output_dir)

if __name__ == "__main__":
    main()