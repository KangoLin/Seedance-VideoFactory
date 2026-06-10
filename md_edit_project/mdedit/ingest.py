import os
import json
from mdedit.ffmpeg import get_video_info


def ingest(input_patterns: list[str], work_dir: str) -> list[dict]:
    clips = []
    for pattern in input_patterns:
        import glob
        paths = sorted(glob.glob(pattern))
        for p in paths:
            if not p.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
                continue
            info = get_video_info(p)
            clip = {
                "id": os.path.splitext(os.path.basename(p))[0],
                "path": os.path.abspath(p),
                "duration": info["duration"],
                "width": info["width"],
                "height": info["height"],
                "fps": info["fps"],
            }
            clips.append(clip)
    clips.sort(key=lambda c: c["id"])
    manifest_path = os.path.join(work_dir, "manifest.json")
    os.makedirs(work_dir, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(clips, f, ensure_ascii=False, indent=2)
    return clips
