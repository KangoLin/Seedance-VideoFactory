import json
import os
from mdedit.llm import call_llm_json
from mdedit.cache import Cache
from mdedit.languages import LANGUAGE_NAMES


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "edit_plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "order": {"type": "integer"},
                    "trim_start": {"type": "number"},
                    "trim_end": {"type": "number"},
                    "fade_in": {"type": "boolean"},
                    "fade_out": {"type": "boolean"},
                    "speed": {"type": "number"},
                    "text_overlay": {"type": "string"},
                    "text_position": {"type": "string"},
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
                            "in": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "duration_sec": {"type": "number"},
                                },
                            },
                            "out": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "duration_sec": {"type": "number"},
                                },
                            },
                        },
                    },
                    "subtitle_style": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "position": {"type": "string"},
                            "font_size": {"type": "integer"},
                            "color": {"type": "string"},
                            "outline_color": {"type": "string"},
                            "animation": {"type": "string"},
                            "start_sec": {"type": "number"},
                            "end_sec": {"type": "number"},
                        },
                    },
                    "color_grade": {
                        "type": "object",
                        "properties": {
                            "brightness": {"type": "number"},
                            "contrast": {"type": "number"},
                            "saturation": {"type": "number"},
                            "temperature": {"type": "string"},
                        },
                    },
                    "emphasis_moments": {
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
                "required": [
                    "clip_id", "order", "trim_start", "trim_end",
                    "fade_in", "fade_out", "speed",
                    "text_overlay", "text_position",
                ],
            },
        },
        "bgm_plan": {
            "type": "object",
            "properties": {
                "mood": {"type": "string"},
                "style": {"type": "string"},
            },
            "required": ["mood", "style"],
        },
        "audit": {
            "type": "object",
            "properties": {
                "overall_quality": {"type": "string"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "suggestions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["overall_quality", "issues", "suggestions"],
        },
    },
    "required": ["edit_plan", "bgm_plan", "audit"],
}

SYSTEM_PROMPT = """You are a professional manga-drama video editor. Given the analysis of each clip and the attached video frames, create a complete episode editing plan.
Output JSON with:
- edit_plan: ordered list of clips with trimming, fading, speed adjustment, text overlay, effects, transitions, subtitle style, color grading, and emphasis moments
- bgm_plan: recommended background music mood and style
- audit: overall quality assessment, issues, and suggestions — use the video frames to evaluate visual quality, composition, character expression, and scene coherence

When supervisor suggestions are provided, incorporate them into the edit_plan. You may adjust intensity, timing, or omit suggestions that don't fit the overall episode flow.

IMPORTANT: All text_overlay and subtitle text must follow the target language specified in the user prompt."""


def plan_episode(
    analyses: list[dict],
    supervisor_prompt: str,
    cache: Cache,
    force: bool = False,
    provider: str = "deepseek",
    frame_paths: list[str] | None = None,
    supervisor_suggestions: list[dict] | None = None,
    target_language: str = "en",
) -> dict:
    cache_key = f"plan:{hash(json.dumps(analyses, sort_keys=True))}:{provider}"
    if not force:
        cached = cache.get(cache_key)
        if cached:
            return cached

    analyses_text = json.dumps(analyses, ensure_ascii=False, indent=2)
    schema_hint = ""
    if provider == "deepseek":
        schema_hint = "\n\nYou MUST output valid JSON matching: " + json.dumps(
            PLAN_SCHEMA, ensure_ascii=False
        )

    suggestions_text = ""
    if supervisor_suggestions:
        suggestions_map = {}
        for s in supervisor_suggestions:
            cid = str(s.get("id", ""))
            sug = s.get("supervisor_suggestions", {})
            if sug:
                suggestions_map[cid] = sug
        if suggestions_map:
            suggestions_text = (
                "\n\nSupervisor suggestions (per clip_id, incorporate into edit_plan):\n"
                + json.dumps(suggestions_map, ensure_ascii=False, indent=2)
            )

    from .languages import get_language_instruction
    lang_instruction = get_language_instruction(target_language)

    user_prompt = f"""Here are the per-clip analyses:

{analyses_text}
{suggestions_text}

Supervisor instructions:
{supervisor_prompt}

Target language: {lang_instruction}

Create the complete episode edit plan.
Incorporate the supervisor suggestions (effects, transitions, subtitle_style, color_grade, emphasis_moments) into the edit_plan where appropriate. You may adjust or omit suggestions that don't fit the overall episode flow.
All text_overlay and subtitle text must be written in {LANGUAGE_NAMES.get(target_language, 'English')} ({target_language}).{schema_hint}"""

    result = call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_schema=PLAN_SCHEMA,
        temperature=0.3,
        provider=provider,
        frame_paths=frame_paths,
    )
    cache.set(cache_key, result)
    return result
