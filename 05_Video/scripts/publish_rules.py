"""Platform publish profile validation and export helpers."""

from __future__ import annotations

from copy import deepcopy

SUPPORTED_PLATFORMS = ["douyin", "youtube", "tiktok", "x", "custom"]
SUPPORTED_LANGUAGES = ["zh-CN", "zh-TW", "en", "ja"]

RISK_WORDS = [
    "最强",
    "绝对",
    "包过",
    "稳赚",
]

PLATFORM_RULES = {
    "douyin": {"title_max": 55, "desc_max": 120, "hashtags_max": 10},
    "youtube": {"title_max": 100, "desc_max": 5000, "hashtags_max": 15},
    "tiktok": {"title_max": 150, "desc_max": 2200, "hashtags_max": 12},
    "x": {"title_max": 100, "desc_max": 280, "hashtags_max": 8},
    "custom": {"title_max": 120, "desc_max": 1000, "hashtags_max": 12},
}


def default_publish_profile() -> dict:
    return {
        "targets": [],
        "default_language": "zh-CN",
        "content": {"title": "", "description": "", "hashtags": []},
        "platform_overrides": {},
        "last_validation_result": {"platforms": {}, "summary": {"pass": 0, "warn": 0, "block": 0}},
        "updated_at": "",
    }


def normalize_publish_profile(profile: dict | None) -> dict:
    base = default_publish_profile()
    data = deepcopy(base)
    if isinstance(profile, dict):
        data.update(profile)
    if data.get("default_language") not in SUPPORTED_LANGUAGES:
        data["default_language"] = "zh-CN"
    targets = [str(x).strip().lower() for x in data.get("targets", []) if str(x).strip()]
    data["targets"] = [x for x in targets if x in SUPPORTED_PLATFORMS]

    content = data.get("content", {})
    if not isinstance(content, dict):
        content = {}
    data["content"] = {
        "title": str(content.get("title", "")).strip(),
        "description": str(content.get("description", "")).strip(),
        "hashtags": normalize_hashtags(content.get("hashtags")),
    }
    overrides = data.get("platform_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    normalized_overrides: dict[str, dict] = {}
    for platform, value in overrides.items():
        key = str(platform).strip().lower()
        if key not in SUPPORTED_PLATFORMS or not isinstance(value, dict):
            continue
        normalized_overrides[key] = {
            "language": value.get("language")
            if value.get("language") in SUPPORTED_LANGUAGES
            else data["default_language"],
            "title": str(value.get("title", "")).strip(),
            "description": str(value.get("description", "")).strip(),
            "hashtags": normalize_hashtags(value.get("hashtags")),
        }
    data["platform_overrides"] = normalized_overrides
    if not isinstance(data.get("last_validation_result"), dict):
        data["last_validation_result"] = deepcopy(base["last_validation_result"])
    data["updated_at"] = str(data.get("updated_at", "")).strip()
    return data


def normalize_hashtags(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = [x.strip() for x in value.replace("，", ",").split(",")]
    else:
        raw = []
    cleaned: list[str] = []
    for item in raw:
        tag = str(item).strip().lstrip("#")
        if not tag:
            continue
        cleaned.append(tag)
    return cleaned


def merge_content(profile: dict, platform: str) -> dict:
    content = deepcopy(profile["content"])
    override = profile.get("platform_overrides", {}).get(platform, {})
    if override:
        if override.get("title"):
            content["title"] = override["title"]
        if override.get("description"):
            content["description"] = override["description"]
        if override.get("hashtags"):
            content["hashtags"] = normalize_hashtags(override["hashtags"])
        content["language"] = override.get("language") or profile["default_language"]
    else:
        content["language"] = profile["default_language"]
    return content


def validate_publish_profile(profile: dict, video_meta: dict | None = None) -> dict:
    data = normalize_publish_profile(profile)
    targets = data["targets"]
    video_meta = video_meta or {}
    duration = int(video_meta.get("duration", 0) or 0)
    ratio = str(video_meta.get("ratio", "")).strip()

    result = {"platforms": {}, "summary": {"pass": 0, "warn": 0, "block": 0}}
    for platform in targets:
        rules = PLATFORM_RULES.get(platform, PLATFORM_RULES["custom"])
        content = merge_content(data, platform)
        issues = []
        title = content["title"]
        desc = content["description"]
        tags = normalize_hashtags(content.get("hashtags"))

        if not title:
            issues.append(issue("block", "title_required", "标题不能为空", "请填写平台标题"))
        elif len(title) > rules["title_max"]:
            issues.append(
                issue(
                    "block",
                    "title_too_long",
                    f"标题长度 {len(title)} 超过限制 {rules['title_max']}",
                    "请缩短标题",
                )
            )

        if len(desc) > rules["desc_max"]:
            issues.append(
                issue(
                    "warn",
                    "description_too_long",
                    f"简介长度 {len(desc)} 超过建议 {rules['desc_max']}",
                    "建议精简简介，避免被截断",
                )
            )
        if len(tags) > rules["hashtags_max"]:
            issues.append(
                issue(
                    "warn",
                    "hashtags_too_many",
                    f"标签数量 {len(tags)} 超过建议 {rules['hashtags_max']}",
                    "减少标签数量，保留核心话题",
                )
            )
        for tag in tags:
            if len(tag) > 24:
                issues.append(issue("warn", "hashtag_too_long", f"标签过长：#{tag}", "建议缩短单个标签"))
                break
        text_all = f"{title}\n{desc}"
        hit_words = [w for w in RISK_WORDS if w in text_all]
        if hit_words:
            issues.append(
                issue(
                    "warn",
                    "risk_words",
                    f"命中风险词：{'、'.join(hit_words)}",
                    "建议改为更中性描述，降低审核风险",
                )
            )
        if duration and (duration < 4 or duration > 180):
            issues.append(
                issue(
                    "warn",
                    "duration_unusual",
                    f"视频时长 {duration}s 可能不适合该平台分发",
                    "建议控制在 8-60 秒内",
                )
            )
        if ratio and ratio not in {"9:16", "16:9", "1:1", "3:4", "4:3"}:
            issues.append(issue("warn", "ratio_unknown", f"比例 {ratio} 不是常见平台比例", "建议改为 9:16 或 16:9"))

        if not issues:
            issues.append(issue("pass", "ok", "规则检查通过", "可直接用于发布"))
        for item in issues:
            result["summary"][item["level"]] = result["summary"].get(item["level"], 0) + 1
        result["platforms"][platform] = {
            "language": content.get("language", data["default_language"]),
            "content": content,
            "issues": issues,
        }
    return result


def build_publish_export(episode: dict, profile: dict, validation_result: dict) -> dict:
    data = normalize_publish_profile(profile)
    payload = {
        "episode_id": episode.get("id"),
        "episode_title": episode.get("title"),
        "episode_no": episode.get("episode_no"),
        "targets": data["targets"],
        "default_language": data["default_language"],
        "platforms": {},
        "validation": validation_result,
    }
    for platform in data["targets"]:
        payload["platforms"][platform] = {
            "language": merge_content(data, platform).get("language", data["default_language"]),
            "title": merge_content(data, platform).get("title", ""),
            "description": merge_content(data, platform).get("description", ""),
            "hashtags": merge_content(data, platform).get("hashtags", []),
        }
    return payload


def issue(level: str, code: str, message: str, suggestion: str) -> dict:
    return {
        "level": level,
        "code": code,
        "message": message,
        "suggestion": suggestion,
    }
