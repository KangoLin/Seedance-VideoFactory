import json
import logging
import os
import re
from pathlib import Path

from .llm import call_llm_json, _call_gemini_text
from .ffmpeg import trim_video, get_video_info, extract_frames

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"
EPISODES_PATH = Path(__file__).resolve().parent.parent.parent / "05_Video" / "workspace" / "episodes.json"

MIN_CLIP_DURATION = 3
MAX_CLIP_DURATION = 180
MIN_SCORE = 60


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {path}")


def _load_episode_tasks(video_name: str) -> list[dict]:
    """Read episodes.json and extract tasks matching the concat video name."""
    if not EPISODES_PATH.exists():
        logger.warning(f"episodes.json not found at {EPISODES_PATH}")
        return []

    with open(EPISODES_PATH, encoding="utf-8") as f:
        data = json.load(f)

    m = re.search(r"ep-(\d+)", video_name)
    if not m:
        logger.warning(f"Cannot parse episode number from video name: {video_name}")
        return []
    target_ep_no = int(m.group(1))

    episode = None
    for ep in data.get("episodes", []):
        if ep.get("episode_no") == target_ep_no:
            episode = ep
            break
    if not episode:
        logger.warning(f"Episode {target_ep_no} not found in episodes.json")
        return []

    tasks = episode.get("tasks", [])
    clip_analysis = episode.get("clip_analysis", {})
    clip_configs = clip_analysis.get("clip_configs", []) if clip_analysis else []

    result = []
    current_time = 0.0
    for i, task in enumerate(tasks):
        dur = float(task.get("duration", 5))
        text_overlays = []
        if i < len(clip_configs):
            text_overlays = clip_configs[i].get("text_overlays", [])
        overlay_text = "; ".join(o.get("text", "") for o in text_overlays) if text_overlays else ""

        result.append({
            "index": i,
            "lane_id": task.get("lane_id", f"task-{i}"),
            "prompt": task.get("prompt", ""),
            "text_overlay": overlay_text,
            "duration": dur,
            "start_sec": current_time,
            "end_sec": current_time + dur,
        })
        current_time += dur

    return result


def _build_llm_context(tasks: list[dict]) -> str:
    lines = []
    for t in tasks:
        lines.append(f"[{t['start_sec']:.1f}s - {t['end_sec']:.1f}s] Scene {t['index'] + 1}")
        if t["text_overlay"]:
            lines.append(f"  On-screen text: {t['text_overlay']}")
        if t["prompt"]:
            lines.append(f"  Description: {t['prompt'][:200]}")
        lines.append("")
    return "\n".join(lines)


def run_pipeline(
    video_path: str,
    work_dir: str,
    cache=None,
    force: bool = False,
    provider: str = "gemini",
    min_score: int = MIN_SCORE,
    max_clips: int = 10,
    progress_callback=None,
    tasks_override: list[dict] | None = None,
    enhance: bool = False,
    enhance_template: str = "douyin",
    supervisor: bool = True,
) -> dict:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = work_dir / "manifest.json"
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    def _progress(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    # ---- Step 1: Load tasks ----
    video_name = os.path.basename(video_path)
    tasks = tasks_override if tasks_override else _load_episode_tasks(video_name)
    if not tasks:
        raise RuntimeError(
            f"No task data found for {video_name}. "
            f"Could not read from {EPISODES_PATH}"
        )
    _progress(f"Loaded {len(tasks)} scenes from episode metadata")

    # ---- Step 1.5: Extract keyframes ----
    frame_paths = None
    if provider == "gemini":
        _progress("Extracting keyframes for Gemini Vision analysis...")
        frame_dir = work_dir / "frames"
        timestamps = [(t["start_sec"] + t["end_sec"]) / 2 for t in tasks]
        frame_paths = extract_frames(video_path, timestamps, str(frame_dir), "scene")
        _progress(f"Extracted {len(frame_paths)} keyframes for {len(tasks)} scenes")

    # ---- Step 2: LLM analysis ----
    _progress("Analyzing scenes with Gemini Vision..." if provider == "gemini" else "Analyzing scenes with LLM...")
    context = _build_llm_context(tasks)
    total_duration = tasks[-1]["end_sec"]

    system_prompt = _load_prompt("analyze_vision.txt" if provider == "gemini" else "analyze_combined.txt")
    if provider == "gemini":
        user_prompt = f"Video duration: {total_duration:.1f}s\n\nThe {len(tasks)} images above are keyframes of each scene in order. Each image corresponds to the scene description below with the same index:\n\n{context}"
    else:
        user_prompt = f"Video duration: {total_duration:.1f}s\n\nScene descriptions (with timestamps):\n\n{context}"

    response_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "score": {"type": "integer"},
                "reason": {"type": "string"},
                "scene_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Indices of scene(s) this clip covers (0-based)",
                },
            },
            "required": ["id", "title", "description", "start_time", "end_time", "score", "reason", "scene_indices"],
        },
    }

    _raw_analysis_holder = []
    raw_clips = call_llm_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=response_schema,
        frame_paths=frame_paths,
        provider=provider,
        temperature=0.3,
        _raw_output=_raw_analysis_holder,
    )
    raw_analysis_text = _raw_analysis_holder[0] if _raw_analysis_holder else ""

    from .srt_utils import time_to_seconds

    scene_descriptions = {}
    for t in tasks:
        idx = t.get("index")
        if idx is not None:
            prompt = (t.get("prompt") or "").strip()
            dur = t.get("duration", 0)
            scene_descriptions[idx] = f"场景{idx}: 时长{dur}秒, 描述: {prompt[:200]}"

    clips = []
    if isinstance(raw_clips, dict) and "clips" in raw_clips:
        raw_clips = raw_clips["clips"]
    for rc in (raw_clips or []):
        try:
            start_sec = time_to_seconds(rc.get("start_time", "00:00:00,000"))
            end_sec = time_to_seconds(rc.get("end_time", "00:00:00,000"))
            dur = end_sec - start_sec
            score = int(rc.get("score", 0))
            if dur < MIN_CLIP_DURATION or dur > MAX_CLIP_DURATION:
                continue
            if score < min_score:
                continue
            scene_indices = rc.get("scene_indices", [])
            relevant_scenes = [scene_descriptions.get(i, "") for i in scene_indices]
            clips.append({
                "id": len(clips) + 1,
                "title": rc.get("title", f"Clip {len(clips)+1}"),
                "description": rc.get("description", ""),
                "start_time": rc.get("start_time", "00:00:00,000"),
                "end_time": rc.get("end_time", "00:00:00,000"),
                "start_sec": round(start_sec, 1),
                "end_sec": round(end_sec, 1),
                "duration": round(dur, 1),
                "score": score,
                "reason": rc.get("reason", ""),
                "scene_indices": scene_indices,
                "scene_descriptions": relevant_scenes,
            })
        except Exception as e:
            logger.warning(f"Skipping invalid clip: {rc} - {e}")

    clips = sorted(clips, key=lambda c: c["start_sec"])[:max_clips]
    for i, c in enumerate(clips):
        c["id"] = i + 1

    if not clips:
        raise RuntimeError("No valid clips found above score threshold")

    _progress(f"LLM identified {len(clips)} highlight clips")

    # ---- Step 3: Clip ----
    _progress("Cutting clips with FFmpeg...")
    project_root = Path(work_dir).resolve().parent.parent.parent
    for c in clips:
        out_name = f"clip_{c['id']:03d}_{re.sub(r'[\\\\/:*?\"<>|]', '', c['title'])[:20]}.mp4"
        out_path = str(clips_dir / out_name)
        trim_video(video_path, out_path, c["start_sec"], c["end_sec"])
        thumb_dir = clips_dir / "thumbs"
        extract_frames(video_path, [c["start_sec"]], str(thumb_dir), f"clip_{c['id']:03d}")
        c["video_path"] = str(Path(out_path).relative_to(project_root)).replace("\\", "/")
        thumb_file = thumb_dir / f"clip_{c['id']:03d}_0000.jpg"
        if thumb_file.exists():
            c["thumbnail"] = str(thumb_file.relative_to(project_root)).replace("\\", "/")

    # ---- Step 2.5: Supervisor (AI 监制) ----
    supervisor_suggestions_data = None
    if supervisor and provider == "gemini" and clips:
        _progress("Running AI Supervisor analysis...")
        from .supervisor import run_supervisor
        clips = run_supervisor(
            clips, video_path, str(work_dir), cache, force, provider, _progress
        )
        supervisor_suggestions_data = {
            str(c["id"]): c.get("supervisor_suggestions", {}) for c in clips
        }

    # ---- Step 4: Enhance with HyperFrames (optional) ----
    if enhance and clips:
        _progress("Enhancing clips with subtitles and effects...")
        from mdedit.hyperframes.enhancer import enhance_all as _enhance_all
        clips = _enhance_all(
            clips, str(work_dir), enhance_template, provider, _progress
        )
        _progress(f"Enhanced {len(clips)} clips")

    # ---- Save manifest ----
    video_info = get_video_info(video_path)
    manifest = {
        "video_path": str(Path(video_path).relative_to(project_root)).replace("\\", "/"),
        "duration": video_info["duration"],
        "clips": clips,
        "supervisor_suggestions": supervisor_suggestions_data,
        "raw_analysis": raw_analysis_text,
        "pipeline_summary": {
            "scenes_loaded": len(tasks),
            "clips_found": len(clips),
            "supervisor": supervisor and provider == "gemini",
            "enhanced": enhance,
            "enhance_template": enhance_template if enhance else None,
            "provider": provider,
        },
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    _progress(f"Pipeline complete: {len(clips)} clips generated")
    return manifest


def analyze_clips_gemini(
    work_dir: str,
    progress_callback=None,
) -> dict:
    """Phase 2: Send each clip video to Gemini for detailed analysis."""
    work_dir = Path(work_dir)
    manifest_path = work_dir / "manifest.json"

    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    clips = manifest.get("clips", [])

    def _progress(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    video_prompt = _load_prompt("video_analysis.txt")
    project_root = work_dir.resolve().parent.parent.parent

    for clip in clips:
        clip = _analyze_single_clip(clip, project_root, work_dir, video_prompt, _progress)

    manifest["clips"] = clips
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest


def _analyze_single_clip(
    clip: dict,
    project_root: Path,
    work_dir: Path,
    video_prompt: str,
    progress=None,
) -> dict:
    """Send a single clip to Gemini for analysis. Mutates and returns the clip dict."""
    import re

    def _emit(msg: str):
        logger.info(msg)
        if progress:
            progress(msg)

    clip_id = clip.get("id")
    clip_path = clip.get("video_path", "")
    if not clip_path:
        _emit(f"Clip {clip_id} has no video_path, skipping")
        return clip

    abs_path = str(project_root / clip_path)
    if not os.path.isfile(abs_path):
        _emit(f"Clip {clip_id} video not found: {abs_path}")
        return clip

    _emit(f"Sending clip {clip_id} ({clip.get('title', '')}) to Gemini...")
    try:
        analysis = _call_gemini_text(
            system_prompt=video_prompt,
            user_prompt=(
                f"视频片段时长: {clip.get('duration', 0)}秒\n"
                f"标题: {clip.get('title', '')}\n"
                f"描述: {clip.get('description', '')}\n\n"
                f"请详细分析这个视频片段。"
            ),
            video_path=abs_path,
        )
        clip["gemini_analysis"] = analysis

        m = re.search(
            r"===EDIT_PLAN_START===\s*(\[.*?\])\s*===EDIT_PLAN_END===",
            analysis,
            re.DOTALL,
        )
        if m:
            try:
                clip["gemini_edit_plan"] = json.loads(m.group(1))
                _emit(f"  Clip #{clip_id}: parsed {len(clip['gemini_edit_plan'])} edit commands")
            except json.JSONDecodeError:
                clip["gemini_edit_plan"] = []
        else:
            clip["gemini_edit_plan"] = []

        _emit(f"Clip {clip_id} analysis complete")
    except Exception as e:
        _emit(f"Clip {clip_id} analysis failed: {e}")
        clip["gemini_analysis"] = f"分析失败: {e}"
        clip["gemini_edit_plan"] = []

    return clip


def analyze_single_clip(
    work_dir: str,
    clip_id: int,
    progress_callback=None,
) -> dict:
    """Phase 2 (single): Send one clip video to Gemini for detailed analysis.

    Loads manifest, runs _analyze_single_clip on the matched clip, persists.
    Returns {"clip": <updated clip dict>}.
    """
    work_dir = Path(work_dir)
    manifest_path = work_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    clips = manifest.get("clips", [])
    project_root = work_dir.resolve().parent.parent.parent
    video_prompt = _load_prompt("video_analysis.txt")

    target = None
    for c in clips:
        if c.get("id") == clip_id:
            target = c
            break
    if target is None:
        raise RuntimeError(f"Clip {clip_id} not found in manifest")

    updated = _analyze_single_clip(
        target, project_root, work_dir, video_prompt, progress_callback
    )

    for i, c in enumerate(clips):
        if c.get("id") == clip_id:
            clips[i] = updated
            break
    manifest["clips"] = clips

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {"clip": updated}


def render_smart_cut(
    work_dir: str,
    selected_clip_ids: list[int] | None = None,
    bgm_path: str | None = None,
    output_path: str | None = None,
    progress_callback=None,
) -> dict:
    """简化精剪：只应用 zoom/crop/speed 编辑命令，不需要 supervisor/plan 管线。"""
    from .ffmpeg import change_speed, apply_zoom, apply_crop, concat_videos

    work_dir = Path(work_dir)
    manifest_path = work_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    clips = manifest.get("clips", [])
    if selected_clip_ids:
        clips = [c for c in clips if c["id"] in selected_clip_ids]
    if not clips:
        raise RuntimeError("No clips to render")

    project_root = work_dir.resolve().parent.parent.parent

    def _progress(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    _progress("Starting smart cut render...")
    tmp_dir = work_dir / "_smart_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    processed = []
    for i, clip in enumerate(clips):
        src = clip.get("video_path", "")
        abs_src = str(project_root / src) if src and not os.path.isabs(src) else src
        if not os.path.isfile(abs_src):
            _progress(f"  Clip #{clip['id']}: source not found, skipping")
            continue

        cur = abs_src
        plan = clip.get("gemini_edit_plan", [])
        if plan:
            _progress(f"  Clip #{clip['id']}: applying {len(plan)} edit commands")
        for op_idx, op in enumerate(plan):
            op_type = op.get("op", "")
            out = str(tmp_dir / f"clip{i}_op{op_idx}.mp4")
            try:
                if op_type == "speed":
                    speed_val = float(op.get("speed", 1.0))
                    if speed_val != 1.0:
                        change_speed(cur, out, speed_val)
                        if os.path.isfile(out):
                            cur = out
                elif op_type == "zoom_in":
                    amount = float(op.get("amount", 1.3))
                    apply_zoom(cur, out, "in", amount - 1.0)
                    if os.path.isfile(out):
                        cur = out
                elif op_type == "zoom_out":
                    amount = float(op.get("amount", 0.8))
                    apply_zoom(cur, out, "out", 1.0 - amount)
                    if os.path.isfile(out):
                        cur = out
                elif op_type == "crop":
                    x = int(op.get("x", 0))
                    y = int(op.get("y", 0))
                    w = int(op.get("w", 720))
                    h = int(op.get("h", 1280))
                    apply_crop(cur, out, x, y, w, h)
                    if os.path.isfile(out):
                        cur = out
            except Exception as e:
                _progress(f"  Clip #{clip['id']} op {op_type} failed: {e}")

        processed.append(cur)

    if not processed:
        raise RuntimeError("No segments to render")

    _progress(f"Concatenating {len(processed)} segments...")
    if not output_path:
        output_path = str(work_dir / "smart_cut.mp4")

    if len(processed) == 1:
        if bgm_path and os.path.isfile(bgm_path):
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-i", processed[0], "-i", bgm_path,
                "-filter_complex", "[1:a]volume=0.15[bgm];[0:a][bgm]amix=inputs=2:duration=first",
                "-c:v", "copy", output_path,
            ], capture_output=True, check=True)
        else:
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-i", processed[0], "-c", "copy", output_path],
                         capture_output=True, check=True)
    else:
        concat_path = str(tmp_dir / "_concat.mp4")
        concat_videos(processed, concat_path)
        if bgm_path and os.path.isfile(bgm_path):
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-i", concat_path, "-i", bgm_path,
                "-filter_complex", "[1:a]volume=0.15[bgm];[0:a][bgm]amix=inputs=2:duration=first",
                "-c:v", "copy", output_path,
            ], capture_output=True, check=True)
        else:
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-i", concat_path, "-c", "copy", output_path],
                         capture_output=True, check=True)

    _progress(f"Smart cut complete: {output_path}")
    return {"output_path": output_path}


def render_fine_cut(
    work_dir: str,
    accepted_suggestions: dict | None = None,
    selected_clip_ids: list[int] | None = None,
    bgm_path: str | None = None,
    output_path: str | None = None,
    progress_callback=None,
) -> dict:
    """阶段2：根据用户确认的监制建议执行精剪。"""
    work_dir = Path(work_dir)
    manifest_path = work_dir / "manifest.json"

    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    clips = manifest.get("clips", [])
    if selected_clip_ids:
        clips = [c for c in clips if c["id"] in selected_clip_ids]
        if not clips:
            raise RuntimeError("No clips remain after selected_clip_ids filter")
    video_rel = manifest.get("video_path", "")
    project_root = work_dir.resolve().parent.parent.parent
    video_path = str(project_root / video_rel)

    def _progress(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    _progress("Starting fine-cut render...")

    # Merge accepted_suggestions into clips
    if accepted_suggestions:
        for clip in clips:
            cid = str(clip["id"])
            if cid in accepted_suggestions:
                clip["supervisor_suggestions"] = accepted_suggestions[cid]

    # Build analyses for plan stage
    analyses = []
    for clip in clips:
        analyses.append({
            "id": clip["id"],
            "title": clip.get("title", ""),
            "description": clip.get("description", ""),
            "start_sec": clip.get("start_sec", 0),
            "end_sec": clip.get("end_sec", 0),
            "duration": clip.get("duration", 0),
            "score": clip.get("score", 0),
            "reason": clip.get("reason", ""),
            "supervisor_suggestions": clip.get("supervisor_suggestions", {}),
        })

    from .plan import plan_episode
    from .cache import Cache

    cache_dir = str(work_dir / "cache")
    cache = Cache(cache_dir)

    _progress("Creating edit plan with supervisor suggestions...")
    plan = plan_episode(
        analyses=analyses,
        supervisor_prompt="",
        cache=cache,
        force=True,
        provider="gemini",
        supervisor_suggestions=analyses,
    )
    edit_plan = plan.get("edit_plan", [])
    _progress(f"Edit plan: {len(edit_plan)} segments")

    # Directly inject supervisor subtitle suggestions into edit_plan
    for plan_item in edit_plan:
        cid = str(plan_item.get("clip_id", ""))
        for clip in clips:
            if str(clip["id"]) == cid:
                sug = clip.get("supervisor_suggestions", {})
                if sug.get("subtitle_text") and not plan_item.get("text_overlay"):
                    plan_item["subtitle_text"] = sug["subtitle_text"]
                    plan_item["subtitle_position"] = sug.get("subtitle_position", "bottom")
                    plan_item["subtitle_font_size"] = sug.get("subtitle_font_size", 36)
                if sug.get("effects") and not plan_item.get("effects"):
                    plan_item["effects"] = sug["effects"]
                if sug.get("transitions") and not plan_item.get("transitions"):
                    plan_item["transitions"] = {
                        "in": {"type": sug["transitions"].get("in_type", "cut"), "duration_sec": sug["transitions"].get("in_dur", 0.5)},
                        "out": {"type": sug["transitions"].get("out_type", "cut"), "duration_sec": sug["transitions"].get("out_dur", 0.5)},
                    }
                if sug.get("emphasis") and not plan_item.get("emphasis_moments"):
                    plan_item["emphasis_moments"] = sug["emphasis"]
                break

    if not output_path:
        output_path = str(work_dir / "fine_cut.mp4")

    _progress("Rendering fine-cut video...")
    from .render import render_episode
    render_episode(clips, plan["edit_plan"], bgm_path, output_path, str(work_dir))

    _progress(f"Fine-cut complete: {output_path}")
    return {"output_path": output_path, "plan": plan}
