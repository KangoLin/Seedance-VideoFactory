"""Shared segment mode and duration helpers for batch runner and preflight."""

from __future__ import annotations

from pathlib import Path

from media_refs import (
    MAX_REFERENCE_IMAGES,
    MAX_REFERENCE_VIDEOS,
    is_reference_media,
    media_kind,
)

MODE_KEYFRAMES = "keyframes"
MODE_TEXT = "text"
MODE_REFERENCE = "reference"
MODE_FIRST_FRAME = "first_frame"

VALID_MODES = {MODE_KEYFRAMES, MODE_TEXT, MODE_REFERENCE, MODE_FIRST_FRAME}
MIN_DURATION = 4
MAX_DURATION = 15


def get_segment_mode(segment: dict) -> str:
    mode = str(segment.get("mode", "")).strip().lower()
    if mode in VALID_MODES:
        return mode
    if segment.get("reference") or segment.get("references"):
        return MODE_REFERENCE
    if segment.get("start") and segment.get("end"):
        return MODE_KEYFRAMES
    if segment.get("start") or segment.get("image") or segment.get("first_frame"):
        return MODE_FIRST_FRAME
    return MODE_TEXT


def reference_paths(segment: dict) -> list[str]:
    refs = segment.get("references") or segment.get("reference")
    if not refs:
        return []
    if isinstance(refs, str):
        return [refs]
    if isinstance(refs, list):
        return [str(item) for item in refs if item]
    raise ValueError(f"segment {segment.get('id', '?')}: reference 必须是字符串或字符串数组")


def segment_required_paths(segment: dict) -> list[str]:
    mode = get_segment_mode(segment)
    if mode == MODE_KEYFRAMES:
        paths = []
        for field in ("start", "end"):
            value = segment.get(field, "")
            if value:
                paths.append(str(value))
        return paths
    if mode == MODE_REFERENCE:
        return reference_paths(segment)
    if mode == MODE_FIRST_FRAME:
        value = segment.get("start") or segment.get("image") or segment.get("first_frame")
        return [str(value)] if value else []
    return []


def resolve_duration(segment: dict, asset: dict, defaults: dict, cli_duration: int | None) -> int:
    """优先级：命令行/GUI --duration > 片段 JSON > 素材 > defaults。"""
    sources: list[int | None] = []
    if cli_duration is not None:
        sources.append(cli_duration)
    sources.extend(
        [
            segment.get("duration"),
            asset.get("duration"),
            defaults.get("duration"),
        ]
    )
    for value in sources:
        if value is None:
            continue
        duration = int(value)
        if duration == -1:
            return duration
        if duration < MIN_DURATION or duration > MAX_DURATION:
            raise ValueError(
                f"segment {segment.get('id', '?')}: duration 须在 {MIN_DURATION}-{MAX_DURATION} 秒，"
                f"或设为 -1 由模型自动选择，当前为 {duration}"
            )
        return duration
    return MIN_DURATION


def resolve_generation_params(
    segment: dict,
    asset: dict,
    defaults: dict,
    cli: dict,
) -> dict:
    duration = resolve_duration(segment, asset, defaults, cli["duration"])
    fps = int(segment.get("fps") or asset.get("fps") or defaults.get("fps") or cli["fps"])
    ratio = str(segment.get("ratio") or asset.get("ratio") or defaults.get("ratio") or cli["ratio"])
    resolution = str(
        segment.get("resolution") or asset.get("resolution") or defaults.get("resolution") or cli.get("resolution", "")
    )
    return {"duration": duration, "fps": fps, "ratio": ratio, "resolution": resolution or None}


def validate_segment(segment: dict, asset_id: str) -> None:
    seg_id = segment.get("id", "?")
    mode = get_segment_mode(segment)
    prompt = str(segment.get("prompt", "")).strip()

    if mode == MODE_TEXT:
        if not prompt:
            raise ValueError(f"{asset_id}/{seg_id}: 文生视频需要 prompt")
        return

    if mode == MODE_REFERENCE:
        refs = reference_paths(segment)
        if not refs:
            raise ValueError(f"{asset_id}/{seg_id}: 参考模式需要 reference 或 references")
        images = videos = 0
        for rel in refs:
            if not is_reference_media(rel):
                raise ValueError(
                    f"{asset_id}/{seg_id}: 不支持的参考类型 {Path(rel).suffix!r}，"
                    "支持 png/jpg/jpeg/webp 与 mp4/mov/webm"
                )
            if media_kind(rel) == "video":
                videos += 1
            else:
                images += 1
        if images > MAX_REFERENCE_IMAGES:
            raise ValueError(f"{asset_id}/{seg_id}: 参考图片最多 {MAX_REFERENCE_IMAGES} 个")
        if videos > MAX_REFERENCE_VIDEOS:
            raise ValueError(f"{asset_id}/{seg_id}: 参考视频最多 {MAX_REFERENCE_VIDEOS} 个")
        if not prompt:
            raise ValueError(f"{asset_id}/{seg_id}: 参考模式需要 prompt")
        return

    if mode == MODE_FIRST_FRAME:
        if not segment_required_paths(segment):
            raise ValueError(f"{asset_id}/{seg_id}: 首帧图生视频需要 start、image 或 first_frame")
        return

    if not segment.get("start") or not segment.get("end"):
        raise ValueError(f"{asset_id}/{seg_id}: 首尾帧模式需要 start 与 end")
    if not prompt:
        raise ValueError(f"{asset_id}/{seg_id}: 首尾帧模式需要 prompt")
