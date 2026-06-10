import os
import subprocess
from pathlib import Path
from mdedit.ffmpeg import (
    trim_video, apply_fade, add_text, change_speed, concat_videos,
    apply_zoom, apply_shake, apply_slow_motion, apply_vignette,
    apply_glow, apply_color_grade, apply_crossfade, apply_freeze_frame,
    get_video_info,
)


def _next_seg(tmp_dir: str, prefix: str, counter: list) -> str:
    counter[0] += 1
    return os.path.join(tmp_dir, f"{prefix}_{counter[0]:04d}.mp4")


def render_episode(
    clips: list[dict],
    edit_plan: list[dict],
    bgm_path: str | None,
    output_path: str,
    work_dir: str = ".md_cache",
) -> str:
    tmp_dir = os.path.join(work_dir, "_render_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    ordered = sorted(edit_plan, key=lambda p: p["order"])
    counter = [0]

    processed = []
    for i, plan_item in enumerate(ordered):
        clip = next(
            (c for c in clips if str(c["id"]) == str(plan_item["clip_id"])),
            None,
        )
        if not clip:
            continue
        src = clip.get("video_path", "") or clip.get("path", "")
        if not src:
            continue
        if not os.path.isabs(src):
            src = str(Path(work_dir).resolve().parent.parent.parent / src)
        if not os.path.isfile(src):
            continue

        cur = src

        # 1. Trim
        trim_start = plan_item.get("trim_start", 0)
        trim_end = plan_item.get("trim_end", 0)
        dur = clip.get("duration", 0) - trim_start - trim_end
        if dur <= 0:
            dur = clip.get("duration", 0)
        if trim_start > 0 or trim_end > 0:
            seg = _next_seg(tmp_dir, f"seg_{i:03d}", counter)
            cur = trim_video(cur, seg, trim_start, trim_start + dur)

        # 2. Effects
        for effect in plan_item.get("effects", []):
            etype = effect.get("type", "")
            out = _next_seg(tmp_dir, f"seg_{i:03d}_fx", counter)
            try:
                if etype == "zoom_in":
                    cur = apply_zoom(cur, out, "in", effect.get("intensity", 0.3))
                elif etype == "zoom_out":
                    cur = apply_zoom(cur, out, "out", effect.get("intensity", 0.3))
                elif etype == "slow_motion":
                    cur = apply_slow_motion(cur, out, effect.get("intensity", 0.5))
                elif etype == "shake":
                    cur = apply_shake(cur, out, effect.get("intensity", 0.3))
                elif etype == "vignette":
                    cur = apply_vignette(cur, out, effect.get("intensity", 0.3))
                elif etype == "glow":
                    cur = apply_glow(cur, out, effect.get("intensity", 0.3))
                else:
                    continue
                if os.path.isfile(out):
                    cur = out
            except Exception:
                pass

        # 3. Color grade
        cg = plan_item.get("color_grade", {})
        if cg:
            b = cg.get("brightness", 1.0)
            c = cg.get("contrast", 1.0)
            s = cg.get("saturation", 1.0)
            t = cg.get("temperature", "neutral")
            if b != 1.0 or c != 1.0 or s != 1.0 or t != "neutral":
                out = _next_seg(tmp_dir, f"seg_{i:03d}_cg", counter)
                try:
                    cur = apply_color_grade(cur, out, b, c, s, t)
                    if os.path.isfile(out):
                        cur = out
                except Exception:
                    pass

        # 4. Fade in
        if plan_item.get("fade_in"):
            out = _next_seg(tmp_dir, f"seg_{i:03d}_fi", counter)
            try:
                cur = apply_fade(cur, out, "in")
                if os.path.isfile(out):
                    cur = out
            except Exception:
                pass

        # 5. Fade out
        if plan_item.get("fade_out"):
            out = _next_seg(tmp_dir, f"seg_{i:03d}_fo", counter)
            try:
                cur = apply_fade(cur, out, "out")
                if os.path.isfile(out):
                    cur = out
            except Exception:
                pass

        # 6. Subtitle / text overlay - handle both flat and nested formats
        subtitle = plan_item.get("subtitle_style") or {}
        text = subtitle.get("text", "") or plan_item.get("text_overlay", "")
        pos = subtitle.get("position", "") or plan_item.get("text_position", "")
        font_size = subtitle.get("font_size", 0) or plan_item.get("subtitle_font_size", 0) or 36
        # Also check flat fields from supervisor
        if not text:
            text = plan_item.get("subtitle_text", "")
        if not pos:
            pos = plan_item.get("subtitle_position", "bottom")
        if text:
            out = _next_seg(tmp_dir, f"seg_{i:03d}_txt", counter)
            try:
                cur = add_text(cur, out, text, position=pos, fontsize=font_size)
                if os.path.isfile(out):
                    cur = out
            except Exception:
                pass

        # 7. Speed
        speed = plan_item.get("speed", 1.0)
        if speed != 1.0:
            out = _next_seg(tmp_dir, f"seg_{i:03d}_sp", counter)
            try:
                cur = change_speed(cur, out, speed)
                if os.path.isfile(out):
                    cur = out
            except Exception:
                pass

        # 8. Emphasis moments
        for moment in plan_item.get("emphasis_moments", []):
            if moment.get("effect") == "freeze_frame":
                out = _next_seg(tmp_dir, f"seg_{i:03d}_frz", counter)
                try:
                    cur = apply_freeze_frame(
                        cur, out, moment["time_sec"], moment.get("duration_sec", 2)
                    )
                    if os.path.isfile(out):
                        cur = out
                except Exception:
                    pass

        processed.append(cur)

    if not processed:
        raise RuntimeError("No segments to render")

    # 9. Concat with transitions
    has_transitions = False
    for p in ordered:
        tr = p.get("transitions", {})
        out_type = (tr.get("out") or {}).get("type", "cut")
        if out_type and out_type != "cut":
            has_transitions = True
            break

    if has_transitions and len(processed) > 1:
        concat_path = processed[0]
        for j in range(1, len(processed)):
            tr_out = (ordered[j - 1].get("transitions") or {}).get("out") or {}
            tr_in = (ordered[j].get("transitions") or {}).get("in") or {}
            tr_type = tr_out.get("type", tr_in.get("type", "cut"))
            tr_dur = tr_out.get("duration_sec", tr_in.get("duration_sec", 0.5))
            if tr_type == "cut" or not tr_type:
                tmp_c = _next_seg(tmp_dir, "_cnc", counter)
                concat_videos([concat_path, processed[j]], tmp_c)
                concat_path = tmp_c
            else:
                tmp_x = _next_seg(tmp_dir, "_xf", counter)
                concat_path = apply_crossfade(
                    concat_path, processed[j], tmp_x, tr_type, tr_dur
                )
    else:
        concat_path = _next_seg(tmp_dir, "_cnc", counter)
        concat_videos(processed, concat_path)

    # 10. BGM
    if bgm_path and os.path.isfile(bgm_path):
        cmd = [
            "ffmpeg", "-y",
            "-i", concat_path, "-i", bgm_path,
            "-filter_complex",
            "[1:a]volume=0.15[bgm];[0:a][bgm]amix=inputs=2:duration=first",
            "-c:v", "copy",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
    else:
        cmd = ["ffmpeg", "-y", "-i", concat_path, "-c", "copy", output_path]
        subprocess.run(cmd, capture_output=True, check=True)

    return output_path
