"""Versioned export filenames: {slug}_{platform}_v001.mp4, v002, ..."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / "05_Video" / "exports"
_VERSION_SUFFIX = re.compile(r"_v(\d+)\.mp4$", re.IGNORECASE)


def max_export_version(output_slug: str, platform: str) -> int:
    max_v = 0
    if not EXPORT_DIR.is_dir():
        return 0
    pattern = f"{output_slug}_{platform}_v*.mp4"
    for path in EXPORT_DIR.glob(pattern):
        match = _VERSION_SUFFIX.search(path.name)
        if match:
            max_v = max(max_v, int(match.group(1)))
    return max_v


def next_export_version(output_slug: str, platform: str) -> int:
    return max_export_version(output_slug, platform) + 1


def export_file_path(output_slug: str, platform: str, version: int) -> Path:
    return EXPORT_DIR / f"{output_slug}_{platform}_v{version:03d}.mp4"


def latest_export_rel_path(output_slug: str, platform: str) -> str | None:
    version = max_export_version(output_slug, platform)
    if version < 1:
        return None
    path = export_file_path(output_slug, platform, version)
    if not path.is_file():
        return None
    return str(path.relative_to(ROOT)).replace("\\", "/")


def export_version_label(output_slug: str, platform: str) -> str:
    version = max_export_version(output_slug, platform)
    if version < 1:
        return f"{output_slug}_{platform}_v001"
    return f"{output_slug}_{platform}_v{version:03d}"
