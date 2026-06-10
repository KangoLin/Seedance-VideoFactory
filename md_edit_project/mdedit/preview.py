import os
import subprocess
import json


def render_preview(clips: list[dict], edit_plan: list[dict], output_path: str) -> str:
    tmp_dir = os.path.join(os.path.dirname(output_path), "_preview_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # order clips by plan order
    plan_by_id = {p["clip_id"]: p for p in edit_plan}
    ordered = sorted(edit_plan, key=lambda p: p["order"])

    trimmed = []
    for i, plan_item in enumerate(ordered):
        clip = next(c for c in clips if c["id"] == plan_item["clip_id"])
        src = clip["path"]
        out = os.path.join(tmp_dir, f"seg_{i:04d}.mp4")
        # quick low-res trim
        dur = (clip["duration"] - plan_item["trim_start"] - plan_item["trim_end"])
        if dur <= 0:
            dur = clip["duration"]
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(plan_item["trim_start"]),
            "-i", src,
            "-t", str(dur),
            "-vf", "scale=-2:360",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-an",
            out,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        trimmed.append(out)

    # concat
    list_path = os.path.join(tmp_dir, "list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in trimmed:
            f.write(f"file '{p}'\n")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path
