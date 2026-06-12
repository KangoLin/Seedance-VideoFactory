REGIONS = [
    {"key": "southeast_asia", "label": "东南亚"},
    {"key": "east_asia", "label": "日韩"},
    {"key": "latin_america", "label": "拉美"},
    {"key": "north_america", "label": "北美"},
    {"key": "europe", "label": "欧洲"},
]

LANGUAGES = {
    "en": {"name": "English", "region": "north_america"},
    "es": {"name": "Español", "region": "latin_america"},
    "pt": {"name": "Português", "region": "latin_america"},
    "fr": {"name": "Français", "region": "europe"},
    "de": {"name": "Deutsch", "region": "europe"},
    "it": {"name": "Italiano", "region": "europe"},
    "nl": {"name": "Nederlands", "region": "europe"},
    "ja": {"name": "日本語", "region": "east_asia"},
    "ko": {"name": "한국어", "region": "east_asia"},
    "th": {"name": "ไทย", "region": "southeast_asia"},
    "vi": {"name": "Tiếng Việt", "region": "southeast_asia"},
    "id": {"name": "Bahasa Indonesia", "region": "southeast_asia"},
    "ms": {"name": "Bahasa Melayu", "region": "southeast_asia"},
    "fil": {"name": "Filipino", "region": "southeast_asia"},
}

LANGUAGE_NAMES = {k: v["name"] for k, v in LANGUAGES.items()}

def get_language_instruction(code: str) -> str:
    if code == "en" or code not in LANGUAGES:
        return "All subtitle text must be in English."
    info = LANGUAGES[code]
    return (
        f"All subtitle text must be written in {info['name']} ({code}). "
        f"Translate any non-{info['name']} on-screen text or dialogue concepts "
        f"into natural, concise {info['name']} suitable for short-form video subtitles."
    )
