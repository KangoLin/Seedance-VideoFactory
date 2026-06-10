"""AI 监制模块：调用 Gemini Vision 分析每个高光片段，输出创意剪辑建议。"""

import json
import logging
import os
from pathlib import Path
from mdedit.llm import call_llm_json
from mdedit.ffmpeg import extract_frames, get_video_info

logger = logging.getLogger(__name__)

SUPERVISOR_SCHEMA = {
    "type": "object",
    "properties": {
        "effects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "intensity": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        },
        "transitions": {
            "type": "object",
            "properties": {
                "in_type": {"type": "string"},
                "in_dur": {"type": "number"},
                "out_type": {"type": "string"},
                "out_dur": {"type": "number"},
            },
        },
        "subtitle_text": {"type": "string"},
        "subtitle_position": {"type": "string"},
        "subtitle_font_size": {"type": "integer"},
        "subtitle_animation": {"type": "string"},
        "color_temperature": {"type": "string"},
        "color_brightness": {"type": "number"},
        "color_contrast": {"type": "number"},
        "color_saturation": {"type": "number"},
        "emphasis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "time_sec": {"type": "number"},
                    "effect": {"type": "string"},
                    "duration_sec": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}


def _default_suggestions(clip: dict) -> dict:
    """兜底建议：当 Gemini 失败时使用基础建议。"""
    dur = clip.get("duration", 10)
    return {
        "effects": [],
        "transitions": {"in_type": "fade", "in_dur": 0.5, "out_type": "fade", "out_dur": 0.5},
        "subtitle_text": clip.get("title", ""),
        "subtitle_position": "bottom",
        "subtitle_font_size": 36,
        "subtitle_animation": "fade_in",
        "color_temperature": "neutral",
        "color_brightness": 1.0,
        "color_contrast": 1.0,
        "color_saturation": 1.0,
        "emphasis": [],
    }


def run_supervisor(
    clips: list[dict],
    video_path: str,
    work_dir: str,
    cache,
    force: bool = False,
    provider: str = "gemini",
    progress_callback=None,
) -> list[dict]:
    """
    对每个高光片段调用 Gemini Vision 做监制分析。

    输入 clips 需包含：id, start_sec, end_sec, scene_indices, scene_descriptions
    输出 clips 增加：supervisor_suggestions
    """
    import concurrent.futures

    prompt_path = Path(__file__).parent / "prompts" / "supervisor_vision.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    def _progress(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    def _supervise_one(clip):
        clip_id = clip["id"]
        cache_key = f"supervisor:{clip_id}:{provider}"
        if not force:
            cached = cache.get(cache_key) if cache else None
            if cached:
                return clip_id, cached

        src_path = clip.get("video_path", "")
        full_path = src_path
        if src_path and not os.path.isabs(src_path):
            project_root = Path(work_dir).resolve().parent.parent.parent
            full_path = str(project_root / src_path)
        if not full_path or not os.path.isfile(full_path):
            full_path = video_path

        try:
            info = get_video_info(full_path)
        except Exception as e:
            _progress(f"  Clip #{clip_id}: get_video_info failed: {e}")
            return clip_id, _default_suggestions(clip)

        dur = info.get("duration", clip.get("duration", 10))
        timestamps = [dur * 0.25, dur * 0.5, dur * 0.75]
        frame_dir = os.path.join(work_dir, "frames", f"clip_{clip_id}")
        frame_paths = extract_frames(full_path, timestamps, frame_dir, prefix=f"clip_{clip_id}")

        scene_context = "\n".join(clip.get("scene_descriptions", []))
        user_prompt = (
            f"视频片段 ID: {clip_id}\n"
            f"时长: {dur:.1f}s\n"
            f"片段标题: {clip.get('title', '')}\n"
            f"片段描述: {clip.get('description', '')}\n"
            f"评分: {clip.get('score', 0)}\n"
            f"评分理由: {clip.get('reason', '')}\n\n"
            f"场景信息:\n{scene_context}\n\n"
            f"请为这个片段提供创意剪辑建议。"
        )

        try:
            result = call_llm_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=SUPERVISOR_SCHEMA,
                frame_paths=frame_paths,
                provider="gemini",
                temperature=0.3,
            )
            if not isinstance(result, dict):
                result = _default_suggestions(clip)
            if cache:
                cache.set(cache_key, result, source_paths=[full_path])
            return clip_id, result
        except Exception as e:
            _progress(f"  Clip #{clip_id}: supervisor failed: {e}")
            return clip_id, _default_suggestions(clip)

    _progress(f"Running AI Supervisor on {len(clips)} clips...")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_supervise_one, c): c for c in clips}
        for fut in concurrent.futures.as_completed(futures):
            clip_id, suggestions = fut.result()
            results[clip_id] = suggestions
            _progress(f"  Clip #{clip_id}: supervisor analysis done")

    for clip in clips:
        clip["supervisor_suggestions"] = results.get(clip["id"], _default_suggestions(clip))

    _progress(f"Supervisor complete: {len(clips)} clips analyzed")
    return clips
