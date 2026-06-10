import json
import logging
import os
import shutil
from pathlib import Path

from .renderer import render_overlay
from .composer import build_composition_html
from .subtitle_gen import generate_subtitles

logger = logging.getLogger(__name__)


def _composite(
    clip_path: str,
    overlay_path: str,
    output_path: str,
) -> str:
    import subprocess
    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-i", overlay_path,
        "-filter_complex",
        "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=180)
    return output_path


def enhance_clip(
    clip_info: dict,
    video_path: str,
    work_dir: str,
    template: str = "douyin",
    provider: str = "gemini",
    progress_callback=None,
) -> str:
    import traceback as _tb
    clip_id = clip_info.get("id", 1)
    title = clip_info.get("title", "")
    scene_indices = clip_info.get("scene_indices", [])

    def _progress(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    # Get actual video duration first
    from mdedit.ffmpeg import get_video_info
    try:
        info = get_video_info(video_path)
    except Exception as e:
        _progress(f"get_video_info failed: {e}\n{_tb.format_exc()}")
        raise
    actual_duration = info.get("duration", clip_info.get("duration", 10))
    width = info.get("width", 720)
    height = info.get("height", 1280)

    _progress(f"Generating subtitles for clip #{clip_id} ({actual_duration:.1f}s)...")

    try:
        subtitles = generate_subtitles(
            clip_info, scene_indices, provider, actual_duration
        )
    except Exception as e:
        _progress(f"Subtitle generation failed: {e}\n{_tb.format_exc()}")
        raise

    if not subtitles:
        _progress(f"Skipping clip #{clip_id} (no subtitles)")
        return video_path

    _progress(f"Building composition with {len(subtitles)} captions for {actual_duration:.1f}s...")

    try:
        html = build_composition_html(
            template=template,
            subtitles=subtitles,
            width=width,
            height=height,
            duration=actual_duration,
            title=title,
        )
    except Exception as e:
        _progress(f"build_composition_html failed: {e}\n{_tb.format_exc()}")
        raise

    proj_dir = os.path.join(work_dir, "hf_projects", f"clip_{clip_id:03d}")
    try:
        overlay_path = render_overlay(
            html=html,
            project_dir=proj_dir,
            output=f"overlay_clip_{clip_id:03d}.mov",
        )
    except Exception as e:
        _progress(f"render_overlay failed: {e}\n{_tb.format_exc()}")
        raise

    _progress(f"Compositing overlay onto clip #{clip_id}...")

    enhanced_dir = os.path.join(work_dir, "enhanced")
    os.makedirs(enhanced_dir, exist_ok=True)
    out_name = f"enhanced_{clip_id:03d}_{title[:20]}.mp4"
    out_path = os.path.join(enhanced_dir, out_name)

    try:
        _composite(video_path, overlay_path, out_path)
    except Exception as e:
        _progress(f"_composite failed: {e}\n{_tb.format_exc()}")
        raise

    _progress(f"Clip #{clip_id} enhanced: {out_name}")

    project_root = Path(work_dir).parent.parent
    return str(Path(out_path).relative_to(project_root)).replace("\\", "/")


def enhance_all(
    clips: list[dict],
    work_dir: str,
    template: str = "douyin",
    provider: str = "gemini",
    progress_callback=None,
) -> list[dict]:
    for c in clips:
        video_path = c.get("video_path", "")
        if not video_path:
            continue

        full_path = str(Path(os.path.join(Path(work_dir).parent.parent, video_path)).resolve())
        if not os.path.isfile(full_path):
            logger.warning(f"Clip file not found: {full_path}")
            continue

        enhanced_path = enhance_clip(
            c, full_path, work_dir, template, provider, progress_callback
        )
        if enhanced_path and enhanced_path != video_path:
            c["enhanced_path"] = enhanced_path
    return clips
