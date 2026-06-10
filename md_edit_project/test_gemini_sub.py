import json, os, sys
sys.path.insert(0, r"E:\seedance_video_toolkit\md_edit_project")
os.chdir(r"E:\seedance_video_toolkit\md_edit_project")
from mdedit.llm import _call_gemini

result = _call_gemini(
    system_prompt="Generate Chinese subtitles as JSON array",
    user_prompt='Generate 2 subtitles for a 5 second clip about combat animation. Return: [{"text": "string", "start": 0, "end": 2.5}]',
    response_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "start": {"type": "number"},
                "end": {"type": "number"},
            },
            "required": ["text", "start", "end"],
        },
    },
    temperature=0.7,
)
print("Result type:", type(result))
print("Result:", json.dumps(result, ensure_ascii=False, indent=2))
