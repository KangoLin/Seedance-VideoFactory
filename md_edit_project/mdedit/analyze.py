import os
import json
import logging
import concurrent.futures
from mdedit.llm import call_llm_json
from mdedit.ffmpeg import extract_frames, get_video_info
from mdedit.cache import Cache

logger = logging.getLogger(__name__)


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "clip_id": {"type": "string"},
        "summary": {"type": "string"},
        "quality_issues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "recommended_trim_start": {"type": "number"},
        "recommended_trim_end": {"type": "number"},
        "mood": {"type": "string"},
        "pace": {"type": "string", "enum": ["slow", "medium", "fast"]},
        "has_dialogue": {"type": "boolean"},
        "text_overlay": {"type": "string"},
        "text_position": {"type": "string"},
        "suggested_effects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
        "suggested_subtitle_style": {"type": "string"},
        "suggested_color_temperature": {"type": "string"},
    },
    "required": [
        "clip_id", "summary", "quality_issues",
        "recommended_trim_start", "recommended_trim_end",
        "mood", "pace", "has_dialogue",
        "text_overlay", "text_position",
    ],
}

SYSTEM_PROMPT = """You are a professional manga-drama video editor. Analyze the provided video frames and output a JSON analysis with:
- clip_id: the clip identifier
- summary: brief description of what happens in the clip (Chinese)
- quality_issues: list of any quality problems (blurry, overexposed, etc.)
- recommended_trim_start: seconds to trim from the beginning (0 if none)
- recommended_trim_end: seconds to trim from the end (0 if none)
- mood: overall mood of the clip (e.g., "紧张", "温馨", "搞笑", "激烈", "平静", "悲伤")
- pace: perceived pace (slow/medium/fast)
- has_dialogue: whether the clip appears to contain dialogue
- text_overlay: suggested subtitle or text overlay text (Chinese)
- text_position: where to place text overlay ("top", "bottom", "center", "none")
- suggested_effects: list of recommended effects with type and reason (optional)
- suggested_subtitle_style: recommended subtitle style description (optional)
- suggested_color_temperature: "warm", "cool", or "neutral" (optional)"""


def analyze_clip(clip: dict, cache: Cache, force: bool = False) -> dict:
    cache_key = f"analyze:{clip['id']}"
    if not force:
        cached = cache.get(cache_key)
        if cached:
            return cached

    frame_dir = os.path.join(os.path.dirname(cache.cache_dir), "frames", clip["id"])
    info = get_video_info(clip["path"])
    dur = info["duration"]
    timestamps = [dur * 0.25, dur * 0.75]
    frame_paths = extract_frames(clip["path"], timestamps, frame_dir, prefix=clip["id"])

    try:
        result = call_llm_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=f"Analyze this clip (id: {clip['id']}, duration: {dur:.1f}s).",
            response_schema=ANALYSIS_SCHEMA,
            frame_paths=frame_paths,
            provider="gemini",
        )
    except Exception as e:
        logger.warning("Analyze via Gemini failed (%s), using default analysis", e)
        result = {
            "clip_id": clip["id"],
            "type": "dialogue",
            "summary": clip.get("name", "unknown"),
            "characters": ["unknown"],
            "text_content": "",
            "text_position": "bottom",
            "bgm_vibe": "neutral",
            "visual_elements": ["generic scene"],
        }
    cache.set(cache_key, result, source_paths=[clip["path"]])
    return result


def analyze_all(
    clips: list[dict], cache: Cache, force: bool = False, max_workers: int = 4
) -> list[dict]:
    analyses = [None] * len(clips)

    def _analyze(i: int):
        return i, analyze_clip(clips[i], cache, force)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_to_i = {pool.submit(_analyze, i): i for i in range(len(clips))}
        for fut in concurrent.futures.as_completed(fut_to_i):
            i, result = fut.result()
            analyses[i] = result

    manifest_dir = os.path.dirname(cache.cache_dir) if cache.cache_dir else "."
    analyses_path = os.path.join(manifest_dir, "analyses.json")
    with open(analyses_path, "w", encoding="utf-8") as f:
        json.dump(analyses, f, ensure_ascii=False, indent=2)
    return analyses
