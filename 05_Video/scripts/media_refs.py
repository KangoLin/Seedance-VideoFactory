"""Reference image/video path helpers."""

from __future__ import annotations

from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}

MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_VIDEOS = 3


def suffix(path: str | Path) -> str:
    return Path(path).suffix.lower()


def is_image_file(path: str | Path) -> bool:
    return suffix(path) in IMAGE_SUFFIXES


def is_video_file(path: str | Path) -> bool:
    return suffix(path) in VIDEO_SUFFIXES


def is_reference_media(path: str | Path) -> bool:
    return is_image_file(path) or is_video_file(path)


def media_kind(path: str | Path) -> str:
    if is_image_file(path):
        return "image"
    if is_video_file(path):
        return "video"
    raise ValueError(f"不支持的参考文件类型：{Path(path).suffix or '(无扩展名)'}")


def max_bytes_for_kind(kind: str) -> int:
    if kind == "video":
        return MAX_VIDEO_BYTES
    return MAX_IMAGE_BYTES
