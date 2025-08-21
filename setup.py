#!/usr/bin/env python
"""
Setup script for vidtools package.

This file exists for backward compatibility with older pip versions.
The actual package configuration is in pyproject.toml.
"""

from setuptools import setup, find_packages
import os

# Read version from _version.py
version_file = os.path.join(os.path.dirname(__file__), "vidtools", "_version.py")
with open(version_file) as f:
    exec(f.read())

# Read README for long description
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="vidtools",
    version=__version__,
    author="Fred Bliss",
    author_email="your.email@example.com",
    description="A comprehensive FFmpeg wrapper for common video processing tasks",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/fredbliss/vidtools",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Multimedia :: Video",
        "Topic :: Multimedia :: Video :: Conversion",
    ],
    python_requires=">=3.8",
    install_requires=[
        "tqdm>=4.65.0",
        "structlog>=23.1.0",
    ],
    extras_require={
        "tui": ["textual>=0.40.0"],
        "dev": [
            "pytest>=7.0",
            "black>=23.0",
            "ruff>=0.1.0",
            "mypy>=1.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "vidtools=vidtools.cli:main",
            "vt=vidtools.cli:main",  # Short alias
        ],
    },
)