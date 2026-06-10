import json, os, sys, traceback
sys.path.insert(0, r"E:\seedance_video_toolkit\md_edit_project")
os.chdir(r"E:\seedance_video_toolkit\md_edit_project")

from mdedit.hyperframes.subtitle_gen import generate_subtitles

# Test with clip 2 data
clip_info = {
    "id": 2,
    "title": "发现终极武器",
    "description": "A combat animation scene",
    "reason": "High impact action",
    "duration": 5.0,
}
scene_indices = [3, 4, 5]

print("Testing generate_subtitles for clip #2...")
try:
    subs = generate_subtitles(clip_info, scene_indices, "gemini", 5.0)
    print(f"Result: {len(subs)} subtitles")
    for s in subs:
        print(f"  {s}")
except Exception as e:
    print(f"EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
