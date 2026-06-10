import json
import logging
import os
from pathlib import Path

from mdedit.llm import call_llm_json

logger = logging.getLogger(__name__)

EPISODES_PATH = Path(__file__).resolve().parent.parent.parent.parent / "05_Video" / "workspace" / "episodes.json"

SUBTITLE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "字幕文本（中文，15字以内）"},
            "start": {"type": "number", "description": "字幕开始时间（秒）"},
            "end": {"type": "number", "description": "字幕结束时间（秒）"},
        },
        "required": ["text", "start", "end"],
    },
}


def _get_existing_overlays(scene_indices: list[int]) -> list[dict]:
    if not EPISODES_PATH.exists():
        return []
    with open(EPISODES_PATH, encoding="utf-8") as f:
        data = json.load(f)

    overlays = []
    for ep in data.get("episodes", []):
        tasks = ep.get("tasks", [])
        clip_configs = []
        clip_analysis = ep.get("clip_analysis")
        if clip_analysis:
            clip_configs = clip_analysis.get("clip_configs", [])

        current_time = 0.0
        for i, task in enumerate(tasks):
            dur = float(task.get("duration", 5))
            if i in scene_indices:
                text = ""
                if i < len(clip_configs):
                    texts = [o.get("text", "") for o in clip_configs[i].get("text_overlays", [])]
                    text = "; ".join(t for t in texts if t)
                if text:
                    overlays.append({
                        "text": text,
                        "start": round(current_time, 1),
                        "end": round(current_time + dur, 1),
                    })
            current_time += dur
    return overlays


def _generate_with_llm(
    clip_info: dict,
    provider: str,
    scene_indices: list[int],
) -> list[dict]:
    scene_desc = clip_info.get("description", "")
    scene_desc_en = clip_info.get("reason", "")
    duration = clip_info.get("duration", 10)
    scene_context = clip_info.get("scene_descriptions", [])
    prompt = (
        f"为一个漫剧高光片段生成字幕，片段时长约{duration}秒。\n"
        f"片段描述：{scene_desc}\n"
        f"评分理由：{scene_desc_en}\n"
    )
    if scene_context:
        prompt += f"片段包含以下场景：\n" + "\n".join(scene_context) + "\n"
    prompt += (
        "要求：\n"
        "1. 生成2-3句字幕，覆盖整个片段\n"
        "2. 每句字幕15字以内\n"
        "3. 字幕时间均匀分布在整个片段中\n"
        "4. 每句字幕2-4秒时长\n"
        "5. 字幕内容要结合场景描述，贴合剧情"
    )

    system_prompt = "你是一个短视频字幕文案专家。根据场景描述生成贴合剧情的字幕文案，适合AI动漫短视频。"
    try:
        result = call_llm_json(
            system_prompt=system_prompt,
            user_prompt=prompt,
            response_schema=SUBTITLE_SCHEMA,
            provider=provider,
            temperature=0.7,
        )
        if isinstance(result, dict) and "subtitles" in result:
            result = result["subtitles"]
        if isinstance(result, list):
            return result
        logger.warning(f"Unexpected subtitle result type: {type(result)}")
        return []
    except Exception as e:
        logger.warning(f"LLM subtitle generation failed: {e}")
        return []


def generate_subtitles(
    clip_info: dict,
    scene_indices: list[int],
    provider: str = "gemini",
    duration: float = 10.0,
) -> list[dict]:
    if "title" not in clip_info:
        clip_info["title"] = clip_info.get("title", f"片段")

    overlays = _get_existing_overlays(scene_indices)
    if overlays:
        logger.info(f"Using {len(overlays)} existing text overlays for clip")
        return overlays

    try:
        result = _generate_with_llm(clip_info, provider, scene_indices)
        if result:
            # Clamp subtitles to actual clip duration
            for s in result:
                s["start"] = max(0, min(s.get("start", 0), duration - 1))
                s["end"] = max(s["start"] + 0.5, min(s.get("end", duration), duration))
            logger.info(f"Generated {len(result)} subtitles via LLM for clip")
            return result
        logger.info("LLM returned no subtitles, using fallback")
    except Exception as e:
        logger.warning(f"LLM subtitle generation failed for clip: {e}")

    return [
        {"text": "精彩片段", "start": 0, "end": min(3, duration)},
        {"text": "不容错过", "start": max(duration - 3, 3), "end": duration},
    ]
