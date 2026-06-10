"""Concat latest export previews for all tasks in one episode."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from export_versions import (
    export_file_path,
    find_latest_export_any_platform,
    find_latest_segment_rel,
    latest_export_rel_path,
)
from workspace_store import ROOT, parse_episode_no, task_output_slug

CONCAT_OUTPUT_DIR = ROOT / "output" / "concat"
_CONCAT_VERSION_RE = re.compile(r"_v(\d+)\.mp4$")

PLATFORM_DEFAULT = "TikTok"


def episode_concat_slug(episode_no: int) -> str:
    return f"ep-{episode_no}-concat"


def resolve_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        winget_packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if winget_packages.is_dir():
            matches = sorted(winget_packages.glob("Gyan.FFmpeg*/**/ffmpeg.exe"))
            if matches:
                return str(matches[0])
    raise RuntimeError("未找到 ffmpeg，请先安装并加入 PATH")


def _concat_list_line(path: Path) -> str:
    resolved = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{resolved}'\n"


def concat_video_files(paths: list[Path], output: Path) -> None:
    if not paths:
        raise ValueError("没有可拼接的视频文件")
    output.parent.mkdir(parents=True, exist_ok=True)
    list_file = output.parent / f"{output.stem}_concat_list.txt"
    list_file.write_text("".join(_concat_list_line(p) for p in paths), encoding="utf-8")
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def latest_export_path_for_task(
    episode_no: int,
    task_index: int,
    task: dict,
    platform: str,
) -> Path | None:
    stored = str(task.get("last_export_path") or "").strip()
    if stored:
        path = (ROOT / stored).resolve()
        if path.is_file():
            return path

    slug = str(task.get("output_slug") or task_output_slug(episode_no, task_index))
    rel = latest_export_rel_path(slug, platform)
    if rel:
        path = (ROOT / rel).resolve()
        if path.is_file():
            return path

    rel = find_latest_export_any_platform(slug)
    if rel:
        path = (ROOT / rel).resolve()
        if path.is_file():
            return path

    rel = find_latest_segment_rel(slug)
    if not rel:
        lane_id = str(task.get("lane_id") or "").strip()
        if lane_id and lane_id != slug:
            rel = find_latest_export_any_platform(lane_id) or find_latest_segment_rel(lane_id)
    if rel:
        path = (ROOT / rel).resolve()
        if path.is_file():
            return path
    return None


def concat_episode_previews(
    episode: dict,
    platform: str = PLATFORM_DEFAULT,
) -> tuple[Path, list[str], list[str]]:
    """
    Returns (output_path, included_labels, skipped_labels).
    included_labels like 分镜任务1; order follows episode.tasks array.
    """
    episode_no = parse_episode_no(episode)
    tasks = episode.get("tasks") or []
    if not tasks:
        raise ValueError("该集没有分镜任务")

    included: list[tuple[str, Path]] = []
    skipped: list[str] = []
    for index, task in enumerate(tasks, start=1):
        label = f"分镜任务{index}"
        path = latest_export_path_for_task(episode_no, index, task, platform)
        if path:
            included.append((label, path))
        else:
            skipped.append(label)

    if not included:
        raise ValueError("没有可拼接的预览视频，请先在各分镜任务中正式生成")

    out_slug = episode_concat_slug(episode_no)
    max_v = 0
    if CONCAT_OUTPUT_DIR.is_dir():
        for p in CONCAT_OUTPUT_DIR.glob(f"{out_slug}_v*.mp4"):
            m = _CONCAT_VERSION_RE.search(p.name)
            if m:
                max_v = max(max_v, int(m.group(1)))
    output = CONCAT_OUTPUT_DIR / f"{out_slug}_v{max_v + 1:03d}.mp4"
    concat_video_files([path for _, path in included], output)
    return output, [label for label, _ in included], skipped
