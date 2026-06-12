import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .llm import call_llm_json
from .ffmpeg import get_video_info

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
EPISODES_PATH = ROOT / "05_Video" / "workspace" / "episodes.json"

SUBTITLE_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "subtitles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_index": {"type": "integer"},
                    "text": {"type": "string", "description": "Subtitle text, max 20 words"},
                },
                "required": ["scene_index", "text"],
            },
        }
    },
    "required": ["subtitles"],
}

EDIT_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "zoom": {
                        "type": "string",
                        "enum": ["in", "out", "none"],
                        "description": "Slow zoom in/out or none",
                    },
                    "speed": {
                        "type": "number",
                        "description": "Playback speed (0.7-1.5). <1 = slow motion, >1 = fast forward",
                    },
                    "transition_to_next": {
                        "type": "string",
                        "enum": ["fade", "dissolve", "cut"],
                        "description": "Transition to the next scene",
                    },
                },
                "required": ["index", "zoom", "speed", "transition_to_next"],
            },
        }
    },
    "required": ["scenes"],
}


def _load_scene_tasks(video_name: str) -> tuple[list[dict], str, int]:
    m = re.search(r"ep-(\d+)", video_name)
    if not m:
        raise RuntimeError(f"Cannot parse episode number from: {video_name}")
    target_ep_no = int(m.group(1))

    if not EPISODES_PATH.exists():
        raise RuntimeError(f"episodes.json not found at {EPISODES_PATH}")

    with open(EPISODES_PATH, encoding="utf-8") as f:
        data = json.load(f)

    episode = None
    for ep in data.get("episodes", []):
        if ep.get("episode_no") == target_ep_no:
            episode = ep
            break
    if not episode:
        raise RuntimeError(f"Episode {target_ep_no} not found in episodes.json")

    tasks = episode.get("tasks", [])
    result = []
    current_time = 0.0
    for i, task in enumerate(tasks):
        dur = float(task.get("duration", 5))
        result.append({
            "index": i,
            "prompt": task.get("prompt", ""),
            "duration": dur,
            "start_sec": current_time,
            "end_sec": current_time + dur,
        })
        current_time += dur

    return result, episode.get("title", ""), target_ep_no


def _generate_edit_plan(
    tasks: list[dict], intensity: int, provider: str, progress_callback
) -> list[dict]:
    lines = []
    for t in tasks:
        prompt = (t.get("prompt") or "")[:200]
        lines.append(
            f"Scene {t['index']+1} ({t['start_sec']:.1f}s-{t['end_sec']:.1f}s, {t['duration']}s): {prompt}"
        )
    context = "\n".join(lines)

    if intensity >= 2:
        system = (
            "You are a cinematic video editor. For each scene, suggest camera effects:\n"
            "- zoom: 'in' for dramatic emphasis, 'out' for reveal, 'none' for dialogue\n"
            "- speed: 0.7-0.9 for dramatic slow-mo, 1.0 for normal, 1.2-1.5 for action\n"
            "- transition: 'fade' for emotional, 'dissolve' for time passage, 'cut' for action\n"
            "Use variety - not all scenes should have the same effect."
        )
    else:
        system = (
            "You are a video editor. Suggest subtle camera effects:\n"
            "- zoom: mostly 'none', occasional 'in' on important moments\n"
            "- speed: mostly 1.0, occasional 0.85 or 1.15 for variety\n"
            "- transition: mostly 'cut', occasional 'dissolve' or 'fade'\n"
            "Keep effects very subtle."
        )

    user = f"Suggest per-scene editing effects for this episode:\n\n{context}"

    if progress_callback:
        progress_callback("Generating AI edit plan...")

    result = call_llm_json(
        system_prompt=system,
        user_prompt=user,
        response_schema=EDIT_PLAN_SCHEMA,
        provider=provider,
        temperature=0.4,
    )

    if isinstance(result, dict) and "scenes" in result:
        return result["scenes"]

    if progress_callback:
        progress_callback("LLM edit plan failed, using defaults")
    return []


def _generate_cinematic_subtitles(
    tasks: list[dict], provider: str, progress_callback,
    target_language: str = "en",
) -> list[dict]:
    lines = []
    for t in tasks:
        prompt = (t.get("prompt") or "")[:300]
        lines.append(
            f"Scene {t['index']+1} ({t['start_sec']:.1f}s-{t['end_sec']:.1f}s, {t['duration']}s): {prompt}"
        )
    context = "\n".join(lines)

    from .languages import get_language_instruction, LANGUAGE_NAMES
    lang_name = LANGUAGE_NAMES.get(target_language, "English")
    lang_instruction = get_language_instruction(target_language)

    system_prompt = (
        f"You are a subtitle writer for a manga-drama (anime-style AI-generated series). "
        f"Generate one concise {lang_name} subtitle line per scene. "
        f"Each subtitle captures the key action, dialogue, or emotion of that scene in 20 words or fewer. "
        f"Subtitles must be dramatic and narrative, suitable for a cinematic short video."
    )
    user_prompt = (
        f"Generate one {lang_name} subtitle per scene for this episode:\n\n{context}\n\n"
        f"{lang_instruction}\n\n"
        "Output JSON with 'subtitles' array, each with 'scene_index' (0-based) and 'text' (subtitle string)."
    )

    if progress_callback:
        progress_callback("Generating AI subtitles per scene...")

    result = call_llm_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=SUBTITLE_BATCH_SCHEMA,
        provider=provider,
        temperature=0.5,
    )

    if isinstance(result, dict) and "subtitles" in result:
        subtitle_list = []
        for s in result["subtitles"]:
            idx = s.get("scene_index")
            text = s.get("text", "")
            if idx is not None and text and 0 <= idx < len(tasks):
                subtitle_list.append({
                    "text": text,
                    "start": tasks[idx]["start_sec"],
                    "end": tasks[idx]["end_sec"],
                })
        if subtitle_list:
            return subtitle_list

    if progress_callback:
        progress_callback("LLM subtitle generation returned empty, using fallback")
    fallback = []
    for t in tasks:
        prompt = (t.get("prompt") or "")[:80]
        fallback.append({
            "text": prompt if prompt else f"Scene {t['index']+1}",
            "start": t["start_sec"],
            "end": t["end_sec"],
        })
    return fallback


def _build_cinematic_html(
    subtitles: list[dict],
    width: int,
    height: int,
    duration: float,
    episode_title: str,
    episode_no: int,
) -> str:
    bar_h = int(height * 0.08)
    visible_top = bar_h
    visible_bottom = height - bar_h
    subtitle_y = visible_top + int((visible_bottom - visible_top) * 0.72)
    title_y = height // 2 - 40

    elements = []
    anim_lines = []

    elements.append(
        f'<div style="position:absolute;top:0;left:0;width:{width}px;'
        f'height:{bar_h}px;background:#000;z-index:100;pointer-events:none;"></div>'
    )
    elements.append(
        f'<div style="position:absolute;bottom:0;left:0;width:{width}px;'
        f'height:{bar_h}px;background:#000;z-index:100;pointer-events:none;"></div>'
    )

    title_text = f"EP.{episode_no}"
    elements.append(
        f"<div id=\"cin-title\" class=\"clip\" data-start=\"0\" data-duration=\"3.5\" "
        f'style="position:absolute;left:0;width:100%;top:{title_y}px;'
        f'font-size:{max(28, min(48, width//20))}px;'
        f'color:#fff;font-family:sans-serif;font-weight:bold;'
        f'text-align:center;text-shadow:0 0 30px rgba(0,0,0,0.9),0 2px 4px rgba(0,0,0,0.5);'
        f'opacity:0;z-index:50;letter-spacing:2px;">{title_text}</div>'
    )
    anim_lines.append(
        f'tl.fromTo("#cin-title",{{opacity:0,scale:0.7,y:-30}},'
        f'{{opacity:1,scale:1,y:0,duration:1.0,ease:"power3.out"}},0);'
    )
    anim_lines.append('tl.to("#cin-title",{opacity:0,duration:0.6,ease:"power2.in"},2.9);')

    for i, sub in enumerate(subtitles):
        start = sub["start"]
        end = sub["end"]
        text = sub["text"]
        fade_out = max(start + 0.5, end - 0.5)

        elements.append(
            f"<div id=\"cin-sub-{i}\" class=\"clip\" "
            f'data-start="{start}" data-duration="{end - start}" '
            f'style="position:absolute;left:8%;width:84%;top:{subtitle_y}px;'
            f'font-size:{max(18, min(32, width//25))}px;'
            f'color:#fff;font-family:sans-serif;'
            f'text-align:center;line-height:1.5;'
            f'text-shadow:0 0 20px rgba(0,0,0,0.95),0 2px 4px rgba(0,0,0,0.8);'
            f'opacity:0;z-index:50;">{text}</div>'
        )
        anim_lines.append(
            f'tl.fromTo("#cin-sub-{i}",{{opacity:0,y:12}},'
            f'{{opacity:1,y:0,duration:0.6,ease:"power2.out"}},{start});'
        )
        anim_lines.append(
            f'tl.to("#cin-sub-{i}",{{opacity:0,duration:0.4,ease:"power2.in"}},{fade_out});'
        )

    anim_js = "const tl = gsap.timeline({ paused: true });\n    " + "\n    ".join(anim_lines)
    anim_js += '\n    window.__timelines = window.__timelines || {};\n    window.__timelines["clip-overlay"] = tl;'

    html = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        "<style>\n"
        "*{margin:0;padding:0;box-sizing:border-box;}\n"
        "html,body{background:transparent;overflow:hidden;}\n"
        f"#root{{position:relative;width:{width}px;height:{height}px;"
        f"overflow:hidden;background:transparent;}}\n"
        "</style>\n"
        '<script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>\n'
        "</head>\n<body>\n"
        f'<div id="root" data-composition-id="clip-overlay" '
        f'data-start="0" data-width="{width}" data-height="{height}" '
        f'data-duration="{duration}">\n'
        + "\n".join(elements)
        + '\n</div>\n<script>\n'
        + anim_js
        + '\n</script>\n</body>\n</html>'
    )
    return html


def _process_segments(
    source_path: str,
    tasks: list[dict],
    edit_plan: list[dict],
    work_dir: Path,
    progress_callback,
) -> str:
    segments_dir = work_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    edit_map = {}
    for e in (edit_plan or []):
        edit_map[e.get("index")] = e

    list_path = work_dir / "segments.txt"
    total_segments = len(tasks)
    processed_durations = []

    with open(list_path, "w", encoding="utf-8") as lst:
        for i, t in enumerate(tasks):
            seg_out = segments_dir / f"seg_{i:04d}.mp4"
            src_start = t["start_sec"]
            src_dur = t["duration"]

            edit = edit_map.get(i, {})
            zoom = edit.get("zoom", "none")
            speed = float(edit.get("speed", 1.0))
            src_speed = speed

            if progress_callback:
                pct = int((i + 1) / total_segments * 100)
                zoom_label = {"in": "zoom+", "out": "zoom-", "none": ""}.get(zoom, "")
                speed_label = f"{speed:.1f}x" if speed != 1.0 else ""
                effects = " ".join(filter(None, [zoom_label, speed_label]))
                progress_callback(f"  Segment {i+1}/{total_segments}: src={src_start:.0f}s dur={src_dur:.0f}s {effects}")

            new_dur = round(src_dur / speed, 2) if speed != 1.0 else src_dur

            filters = []
            if zoom == "in":
                fps = 24
                total_f = max(int(new_dur * fps), 1)
                filters.append(
                    f"zoompan=z='if(eq(on,1),1,min(zoom+0.003,1.10))'"
                    f":d={total_f}:s={t['width']}x{t['height']}:fps={fps}"
                )
            elif zoom == "out":
                fps = 24
                total_f = max(int(new_dur * fps), 1)
                filters.append(
                    f"zoompan=z='if(eq(on,1),1.10,max(zoom-0.003,1.0))'"
                    f":d={total_f}:s={t['width']}x{t['height']}:fps={fps}"
                )

            if speed != 1.0:
                filters.append(f"setpts={1/speed}*PTS")
                af_filter = f"atempo={speed}"
            else:
                af_filter = None

            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{src_start:.3f}",
                "-i", source_path,
                "-t", f"{src_dur:.3f}",
            ]
            if filters:
                cmd.extend(["-vf", ",".join(filters)])
            if af_filter:
                cmd.extend(["-af", af_filter])
            cmd.extend([
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k",
                str(seg_out),
            ])

            try:
                r = subprocess.run(cmd, capture_output=True, timeout=300)
                if r.returncode != 0:
                    raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
            except Exception as e:
                if seg_out.exists():
                    seg_out.unlink()
                if progress_callback:
                    progress_callback(f"  Segment {i+1} failed: {e}, using source copy")
                cmd2 = [
                    "ffmpeg", "-y",
                    "-ss", f"{src_start:.3f}",
                    "-i", source_path,
                    "-t", f"{src_dur:.3f}",
                    "-c", "copy",
                    str(seg_out),
                ]
                subprocess.run(cmd2, capture_output=True, timeout=120)

            lst.write(f"file '{seg_out.resolve().as_posix()}'\n")
            processed_durations.append(new_dur)

    output_video = str(work_dir / "edited_source.mp4")
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        output_video,
    ]
    subprocess.run(concat_cmd, capture_output=True, timeout=300)

    new_total = sum(processed_durations)
    return output_video, new_total


def _apply_cinematic_filters(
    video_path: str,
    overlay_path: str,
    output_path: str,
    bgm_path: str | None = None,
) -> str:
    if bgm_path and os.path.isfile(bgm_path):
        filter_complex = (
            "[0:v]eq=brightness=0.02:contrast=1.12:saturation=0.88[v0];"
            "[v0][1:v]overlay=0:0:format=auto,format=yuv420p[v];"
            "[0:a][2:a]amix=inputs=2:duration=first:weights=1 0.15[a]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", overlay_path,
            "-i", bgm_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ]
    else:
        filter_complex = (
            "[0:v]eq=brightness=0.02:contrast=1.12:saturation=0.88[v0];"
            "[v0][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", overlay_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "copy",
            output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"FFmpeg composite failed: {err}")
    return output_path


def render_cinematic(
    video_path: str,
    work_dir: str,
    output_path: str,
    bgm_path: str | None = None,
    provider: str = "gemini",
    edit_intensity: int = 0,
    target_language: str = "en",
    progress_callback=None,
) -> dict:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    def _progress(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    _progress("Loading scene data from episodes.json...")
    video_name = os.path.basename(video_path)
    tasks, episode_title, episode_no = _load_scene_tasks(video_name)
    total_duration = tasks[-1]["end_sec"] if tasks else 0
    _progress(f"Loaded {len(tasks)} scenes, total duration: {total_duration:.1f}s")

    _progress("Getting video info...")
    info = get_video_info(video_path)
    width = info.get("width", 720)
    height = info.get("height", 1280)
    fps = info.get("fps", 24)
    _progress(f"Video: {width}x{height}, {fps:.1f}fps")

    # Add width/height to tasks for zoompan
    for t in tasks:
        t["width"] = width
        t["height"] = height

    edit_plan = []
    if edit_intensity > 0:
        edit_plan = _generate_edit_plan(tasks, edit_intensity, provider, progress_callback)
        _progress(f"Edit plan: {len(edit_plan)} scenes")

    subtitles = _generate_cinematic_subtitles(tasks, provider, progress_callback, target_language)
    _progress(f"Generated {len(subtitles)} scene subtitles")

    if edit_intensity > 0 and edit_plan:
        _progress("Processing segments with AI editing effects...")
        edited_source, new_duration = _process_segments(
            video_path, tasks, edit_plan, work_dir, progress_callback,
        )
        _progress(f"Edited source: {new_duration:.1f}s (from {total_duration:.1f}s)")

        # Recalculate subtitle timestamps to match edited durations
        edit_map = {}
        for e in edit_plan:
            edit_map[e.get("index")] = e
        new_time = 0.0
        for i, t in enumerate(tasks):
            speed = float(edit_map.get(i, {}).get("speed", 1.0))
            orig_dur = t["duration"]
            new_dur = round(orig_dur / speed, 2) if speed != 1.0 else orig_dur
            for s in subtitles:
                if abs(s["start"] - t["start_sec"]) < 0.01 and abs(s["end"] - t["end_sec"]) < 0.01:
                    s["start"] = round(new_time, 1)
                    s["end"] = round(new_time + new_dur, 1)
                    break
            new_time += new_dur
    else:
        edited_source = video_path
        new_duration = total_duration

    _progress("Building cinematic overlay HTML...")
    html = _build_cinematic_html(
        subtitles, width, height, new_duration,
        episode_title, episode_no,
    )

    from .hyperframes.renderer import render_overlay

    _progress("Rendering overlay with HyperFrames...")
    proj_dir = str(work_dir / "hf_overlay")
    overlay_path = render_overlay(
        html=html,
        project_dir=proj_dir,
        output="cinematic_overlay.mov",
        quality="draft",
    )
    _progress(f"Overlay rendered: {os.path.basename(overlay_path)}")

    _progress("Compositing overlay + color grading...")
    _apply_cinematic_filters(edited_source, overlay_path, output_path, bgm_path)
    _progress(f"Cinematic render complete: {output_path}")

    return {
        "output_path": output_path,
        "scenes": len(tasks),
        "subtitles": len(subtitles),
        "duration": new_duration,
        "original_duration": total_duration,
        "edit_intensity": edit_intensity,
    }
