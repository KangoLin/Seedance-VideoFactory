import json
import subprocess
import os


def _fwd(p: str) -> str:
    return p.replace("\\", "/")


def get_video_info(path: str) -> dict:
    import tempfile
    import uuid
    json_path = os.path.join(tempfile.gettempdir(), f"_ffprobe_{os.getpid()}_{uuid.uuid4().hex[:8]}.json")
    try:
        with open(json_path, "w", encoding="utf-8") as outf:
            p = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                stdout=outf, stderr=subprocess.PIPE, timeout=30,
            )
        if not os.path.isfile(json_path) or os.path.getsize(json_path) == 0:
            raise RuntimeError(
                f"ffprobe failed for {path} (rc={p.returncode}, "
                f"stderr={p.stderr.decode('utf-8','replace')[:300]})"
            )
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
    finally:
        try:
            os.unlink(json_path)
        except Exception:
            pass
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s["codec_type"] == "video"), {})
    afr = video_stream.get("avg_frame_rate", "0/1")
    fps = 0.0
    if "/" in afr:
        parts = afr.split("/")
        try:
            fps = float(parts[0]) / float(parts[1])
        except (ValueError, ZeroDivisionError):
            fps = 0.0
    return {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": fps,
        "codec": video_stream.get("codec_name", "h264"),
    }


def extract_frames(path: str, timestamps: list[float], output_dir: str, prefix: str = "frame") -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    out_paths = []
    for i, ts in enumerate(timestamps):
        out = os.path.join(output_dir, f"{prefix}_{i:04d}.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", path,
            "-vframes", "1",
            "-q:v", "2",
            out,
        ]
        subprocess.run(cmd, capture_output=True)
        out_paths.append(out)
    return out_paths


def trim_video(input_path: str, output_path: str, start: float, end: float) -> str:
    dur = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(dur),
        "-c", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_fade(input_path: str, output_path: str, fade_type: str, duration: float = 0.5) -> str:
    info = get_video_info(input_path)
    dur = info["duration"]
    if fade_type == "in":
        filter_str = f"fade=t=in:st=0:d={duration}"
    elif fade_type == "out":
        filter_str = f"fade=t=out:st={dur-duration}:d={duration}"
    else:
        return input_path
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", filter_str,
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def add_text(
    input_path: str, output_path: str,
    text: str, position: str = "bottom",
    fontsize: int = 36,
) -> str:
    info = get_video_info(input_path)
    w, h = info["width"], info["height"]

    align_map = {"top": 8, "bottom": 2, "center": 5}
    align = align_map.get(position, 2)

    ass_path = output_path + ".ass"
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{fontsize},&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,2,{align},10,10,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:99:00.00,Default,,0,0,0,,{text}
"""
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    ass_rel = os.path.relpath(ass_path).replace("\\", "/")
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", f"subtitles={ass_rel}",
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    try:
        os.remove(ass_path)
    except Exception:
        pass
    return output_path


def change_speed(input_path: str, output_path: str, speed: float) -> str:
    setpts = f"setpts={1/speed}*PTS"
    atempo = f"atempo={speed}"
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", setpts,
        "-af", atempo,
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_crop(
    input_path: str, output_path: str,
    x: int = 0, y: int = 0, width: int = 720, height: int = 1280,
) -> str:
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", f"crop={width}:{height}:{x}:{y}",
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def concat_videos(input_paths: list[str], output_path: str) -> str:
    list_path = os.path.join(os.path.dirname(output_path), "_concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in input_paths:
            f.write(f"file '{_fwd(os.path.abspath(p))}'\n")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    os.remove(list_path)
    return output_path


def add_bgm(video_path: str, bgm_path: str, output_path: str, volume: float = 0.15) -> str:
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume={volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first",
        "-c:v", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_zoom(
    input_path: str, output_path: str,
    zoom_type: str = "in",
    intensity: float = 0.3,
) -> str:
    info = get_video_info(input_path)
    dur = info["duration"]
    fps = info["fps"] or 25
    total_frames = max(int(dur * fps), 1)
    w, h = info["width"], info["height"]
    if zoom_type == "in":
        zoom_expr = f"min(zoom+{intensity / total_frames:.8f},1+{intensity})"
    else:
        zoom_expr = f"max(zoom-{intensity / total_frames:.8f},1.0)"
    vf = (
        f"zoompan=z='{zoom_expr}'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={total_frames}:s={w}x{h}:fps={fps}"
    )
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", output_path]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_shake(
    input_path: str, output_path: str,
    intensity: float = 0.3,
) -> str:
    info = get_video_info(input_path)
    w, h = info["width"], info["height"]
    amp = max(1, int(intensity * 10))
    vf = (
        f"crop={w}:{h}"
        f":x='iw/2-(iw/2)+{amp}*sin(t*15)'"
        f":y='ih/2-(ih/2)+{amp}*cos(t*12)'"
    )
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", output_path]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_slow_motion(
    input_path: str, output_path: str,
    speed: float = 0.5,
) -> str:
    setpts = f"setpts={1 / speed}*PTS"
    parts = []
    remaining = speed
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    parts.append(f"atempo={remaining}")
    af = ",".join(parts)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", setpts, "-af", af, output_path]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_vignette(
    input_path: str, output_path: str,
    intensity: float = 0.3,
) -> str:
    angle = max(0.1, intensity * 3.14)
    vf = f"vignette=PI/{max(1, int(3.14 / angle))}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", output_path]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_glow(
    input_path: str, output_path: str,
    intensity: float = 0.3,
) -> str:
    blur_level = max(1, int(intensity * 20))
    vf = (
        f"split[main][blur];"
        f"[blur]boxblur={blur_level}:{blur_level}[glowed];"
        f"[main][glowed]blend=all_mode=screen:all_opacity={intensity}"
    )
    cmd = ["ffmpeg", "-y", "-i", input_path, "-filter_complex", vf, "-c:a", "copy", output_path]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_color_grade(
    input_path: str, output_path: str,
    brightness: float = 1.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    temperature: str = "neutral",
) -> str:
    filters = []
    if brightness != 1.0 or contrast != 1.0 or saturation != 1.0:
        filters.append(
            f"eq=brightness={brightness - 1:.2f}:contrast={contrast}:saturation={saturation}"
        )
    if temperature == "warm":
        filters.append("colorbalance=rs=0.1:gs=0.05:bs=-0.1:rm=0.05:gm=0.02:bm=-0.05")
    elif temperature == "cool":
        filters.append("colorbalance=rs=-0.1:gs=-0.05:bs=0.1:rm=-0.05:gm=-0.02:bm=0.05")
    if not filters:
        shutil.copy2(input_path, output_path)
        return output_path
    vf = ",".join(filters)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", output_path]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def apply_crossfade(
    input_a: str, input_b: str, output_path: str,
    transition_type: str = "fade",
    duration: float = 0.5,
) -> str:
    info_a = get_video_info(input_a)
    offset = max(0, info_a["duration"] - duration)
    xfade_map = {
        "fade": "fade",
        "dissolve": "dissolve",
        "wipe_left": "wipeleft",
        "wipe_right": "wiperight",
        "zoom_in": "circlecrop",
    }
    xfade_type = xfade_map.get(transition_type, "fade")
    filter_complex = (
        f"[0:v][1:v]xfade=transition={xfade_type}"
        f":duration={duration}:offset={offset}[v];"
        f"[0:a][1:a]acrossfade=d={duration}[a]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", input_a, "-i", input_b,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        # Fallback to simple concat without transition
        concat_videos([input_a, input_b], output_path)
    return output_path


def apply_freeze_frame(
    input_path: str, output_path: str,
    time_sec: float, duration: float = 2.0,
) -> str:
    vf = f"freeze=stop={time_sec}:duration={duration}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", output_path]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path
