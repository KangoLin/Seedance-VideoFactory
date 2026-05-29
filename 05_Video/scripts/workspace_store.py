"""Persist episode / task workspace to local JSON."""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = ROOT / "05_Video" / "workspace"
WORKSPACE_FILE = WORKSPACE_DIR / "episodes.json"
WORKSPACE_VERSION = 1
SEGMENT_DIR = ROOT / "05_Video" / "segments"
EXPORT_DIR = ROOT / "05_Video" / "exports"


def default_task(lane_id: str) -> dict:
    return {
        "lane_id": lane_id,
        "asset": "GUI_QUICK",
        "gen_mode": "reference",
        "prompt": "",
        "duration": 4,
        "ratio": "9:16",
        "references": [],
        "start_frame": None,
        "end_frame": None,
        "image_gen_mode": "text_image",
        "image_gen_prompt": "",
        "image_gen_model": "",
        "image_gen_sources": [],
        "image_gen_generated": [],
    }


def default_episode(episode_id: str, title: str, episode_no: int | None = None) -> dict:
    lane_id = f"{episode_id}-task-1"
    number = episode_no if episode_no is not None else parse_episode_no_from_title(title) or 1
    return {
        "id": episode_id,
        "episode_no": number,
        "title": title,
        "tasks": [default_task(lane_id)],
    }


def parse_episode_no_from_title(title: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*集", str(title))
    if match:
        return int(match.group(1))
    return None


def parse_episode_no(episode: dict) -> int:
    value = episode.get("episode_no")
    if value is not None:
        return int(value)
    from_title = parse_episode_no_from_title(str(episode.get("title", "")))
    if from_title is not None:
        return from_title
    match = re.fullmatch(r"ep-(\d+)", str(episode.get("id", "")))
    if match:
        return int(match.group(1))
    return 1


def suggest_episode_no(workspace: dict) -> int:
    used = {parse_episode_no(ep) for ep in workspace.get("episodes", [])}
    number = 1
    while number in used:
        number += 1
    return number


def episode_no_taken(workspace: dict, episode_no: int, exclude_id: str | None = None) -> bool:
    for episode in workspace.get("episodes", []):
        if exclude_id and episode.get("id") == exclude_id:
            continue
        if parse_episode_no(episode) == episode_no:
            return True
    return False


def sanitize_output_slug(slug: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", str(slug).strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned or "output"


def task_output_slug(episode_no: int, task_index: int) -> str:
    return sanitize_output_slug(f"ep-{episode_no}-task-{task_index}")


def assign_output_slugs_for_episode(episode: dict) -> None:
    episode_no = parse_episode_no(episode)
    for index, task in enumerate(episode.get("tasks", []), start=1):
        task["output_slug"] = task_output_slug(episode_no, index)


def assign_all_output_slugs(workspace: dict) -> None:
    for episode in workspace.get("episodes", []):
        assign_output_slugs_for_episode(episode)


def lane_slug_map(workspace: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for episode in workspace.get("episodes", []):
        for index, task in enumerate(episode.get("tasks", []), start=1):
            lane_id = str(task.get("lane_id", "")).strip()
            if lane_id:
                mapping[lane_id] = str(
                    task.get("output_slug") or task_output_slug(parse_episode_no(episode), index)
                )
    return mapping


def lane_slug_map_from_raw(raw: dict) -> dict[str, str]:
    """Map lane_id -> slug before normalize; falls back to lane_id for legacy files on disk."""
    mapping: dict[str, str] = {}
    for episode in raw.get("episodes", []):
        if not isinstance(episode, dict):
            continue
        for index, task in enumerate(episode.get("tasks", []), start=1):
            if not isinstance(task, dict):
                continue
            lane_id = str(task.get("lane_id", "")).strip()
            if not lane_id:
                continue
            stored = str(task.get("output_slug", "")).strip()
            if stored:
                mapping[lane_id] = sanitize_output_slug(stored)
            else:
                mapping[lane_id] = lane_id
    return mapping


def apply_slug_migrations(old_map: dict[str, str], workspace: dict) -> None:
    new_map = lane_slug_map(workspace)
    for lane_id, new_slug in new_map.items():
        old_slug = old_map.get(lane_id, lane_id)
        if old_slug != new_slug:
            migrate_output_artifacts(old_slug, new_slug)
        if lane_id not in {old_slug, new_slug}:
            migrate_output_artifacts(lane_id, new_slug)


def output_slug_for_lane(workspace: dict, lane_id: str) -> str:
    slug = lane_slug_map(workspace).get(lane_id)
    return slug if slug else lane_id


def migrate_output_artifacts(old_slug: str, new_slug: str) -> None:
    old_slug = sanitize_output_slug(old_slug)
    new_slug = sanitize_output_slug(new_slug)
    if not old_slug or old_slug == new_slug:
        return

    old_seg = SEGMENT_DIR / old_slug
    new_seg = SEGMENT_DIR / new_slug
    if old_seg.is_dir():
        if new_seg.exists():
            backup = SEGMENT_DIR / f"{new_slug}__prev_{old_slug}"
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            shutil.move(str(new_seg), str(backup))
        new_seg.mkdir(parents=True, exist_ok=True)
        for path in list(old_seg.iterdir()):
            dest_name = path.name.replace(old_slug, new_slug) if old_slug in path.name else path.name
            dest = new_seg / dest_name
            if dest.exists():
                continue
            shutil.move(str(path), str(dest))
        try:
            old_seg.rmdir()
        except OSError:
            pass

    if EXPORT_DIR.is_dir():
        for path in EXPORT_DIR.glob(f"{old_slug}_*_v*.mp4"):
            dest = EXPORT_DIR / path.name.replace(old_slug, new_slug, 1)
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))


def normalize_episode_title(episode_no: int, title: str | None) -> str:
    cleaned = str(title or "").strip()
    return cleaned if cleaned else f"第 {episode_no} 集"


def create_episode_entry(workspace: dict, episode_no: int, title: str | None = None) -> dict:
    if episode_no < 1:
        raise ValueError("集数须为大于 0 的整数")
    if episode_no_taken(workspace, episode_no):
        raise ValueError(f"第 {episode_no} 集已存在")
    episode_id = next_episode_id(workspace)
    episode = default_episode(episode_id, normalize_episode_title(episode_no, title), episode_no)
    workspace["episodes"].append(episode)
    return episode


def update_episode_meta(
    workspace: dict,
    episode_id: str,
    episode_no: int | None = None,
    title: str | None = None,
) -> dict:
    episode = find_episode(workspace, episode_id)
    if not episode:
        raise ValueError(f"未知集数: {episode_id}")
    new_no = parse_episode_no(episode) if episode_no is None else int(episode_no)
    if new_no < 1:
        raise ValueError("集数须为大于 0 的整数")
    if episode_no_taken(workspace, new_no, exclude_id=episode_id):
        raise ValueError(f"第 {new_no} 集已存在")
    episode["episode_no"] = new_no
    if title is not None and str(title).strip():
        episode["title"] = str(title).strip()
    else:
        episode["title"] = normalize_episode_title(new_no, episode.get("title"))
    return episode


def default_workspace() -> dict:
    episode_id = "ep-1"
    return {
        "version": WORKSPACE_VERSION,
        "episode_seq": 1,
        "active_episode_id": None,
        "episodes": [default_episode(episode_id, "第 1 集")],
    }


def _rebuild_seq(workspace: dict) -> None:
    max_ep = 0
    for episode in workspace.get("episodes", []):
        match = re.fullmatch(r"ep-(\d+)", str(episode.get("id", "")))
        if match:
            max_ep = max(max_ep, int(match.group(1)))
    workspace["episode_seq"] = max(max_ep, int(workspace.get("episode_seq", 0)))


def normalize_workspace(raw: dict | None) -> dict:
    if not raw or not isinstance(raw, dict):
        return default_workspace()
    workspace = deepcopy(raw)
    workspace["version"] = WORKSPACE_VERSION
    episodes = workspace.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return default_workspace()
    for episode in episodes:
        if not episode.get("id"):
            episode["id"] = "ep-1"
        if not episode.get("title"):
            episode["title"] = f"第 {parse_episode_no(episode)} 集"
        episode["episode_no"] = parse_episode_no(episode)
        tasks = episode.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            episode["tasks"] = [default_task(f"{episode['id']}-task-1")]
        for index, task in enumerate(episode["tasks"], start=1):
            if not task.get("lane_id"):
                task["lane_id"] = f"{episode['id']}-task-{index}"
            task.setdefault("asset", "GUI_QUICK")
            task.setdefault("gen_mode", "reference")
            task.setdefault("prompt", "")
            task.setdefault("duration", 4)
            task.setdefault("ratio", "9:16")
            task.setdefault("references", [])
            task.setdefault("start_frame", None)
            task.setdefault("end_frame", None)
            task.setdefault("image_gen_mode", "text_image")
            if task.get("image_gen_mode") in {"text", "image"}:
                task["image_gen_mode"] = "text_image"
            task.setdefault("image_gen_prompt", "")
            task.setdefault("image_gen_model", "")
            task.setdefault("image_gen_sources", [])
            task.setdefault("image_gen_generated", [])
    active = workspace.get("active_episode_id")
    ids = {ep["id"] for ep in episodes}
    if active not in ids:
        workspace["active_episode_id"] = None
    _rebuild_seq(workspace)
    assign_all_output_slugs(workspace)
    return workspace


def load_workspace() -> dict:
    if not WORKSPACE_FILE.is_file():
        data = default_workspace()
        save_workspace(data)
        return data
    try:
        raw = json.loads(WORKSPACE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = default_workspace()
        save_workspace(data)
        return data
    old_map = lane_slug_map_from_raw(raw) if isinstance(raw, dict) else {}
    data = normalize_workspace(raw)
    apply_slug_migrations(old_map, data)
    WORKSPACE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def save_workspace(workspace: dict) -> dict:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    old_map: dict[str, str] = {}
    if WORKSPACE_FILE.is_file():
        try:
            raw = json.loads(WORKSPACE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                old_map = lane_slug_map_from_raw(raw)
        except (json.JSONDecodeError, OSError):
            old_map = {}
    data = normalize_workspace(workspace)
    apply_slug_migrations(old_map, data)
    WORKSPACE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def next_episode_id(workspace: dict) -> str:
    workspace["episode_seq"] = int(workspace.get("episode_seq", 0)) + 1
    return f"ep-{workspace['episode_seq']}"


def next_task_id(episode: dict) -> str:
    episode_id = episode["id"]
    numbers = []
    for task in episode.get("tasks", []):
        lane_id = str(task.get("lane_id", ""))
        match = re.fullmatch(rf"{re.escape(episode_id)}-task-(\d+)", lane_id)
        if match:
            numbers.append(int(match.group(1)))
    next_no = max(numbers, default=0) + 1
    return f"{episode_id}-task-{next_no}"


def find_episode(workspace: dict, episode_id: str) -> dict | None:
    for episode in workspace.get("episodes", []):
        if episode.get("id") == episode_id:
            return episode
    return None


def all_lane_ids(workspace: dict) -> list[str]:
    lane_ids: list[str] = []
    for episode in workspace.get("episodes", []):
        for task in episode.get("tasks", []):
            lane_id = str(task.get("lane_id", "")).strip()
            if lane_id:
                lane_ids.append(lane_id)
    return lane_ids


def episode_lane_ids(episode: dict) -> list[str]:
    return [str(task.get("lane_id", "")).strip() for task in episode.get("tasks", []) if task.get("lane_id")]


def remove_episode(workspace: dict, episode_id: str) -> None:
    episodes = workspace.get("episodes", [])
    if len(episodes) <= 1:
        raise ValueError("至少保留一个集数")
    workspace["episodes"] = [ep for ep in episodes if ep.get("id") != episode_id]
    if workspace.get("active_episode_id") == episode_id:
        workspace["active_episode_id"] = None


def remove_task(workspace: dict, episode_id: str, lane_id: str) -> None:
    episode = find_episode(workspace, episode_id)
    if not episode:
        raise ValueError(f"未知集数: {episode_id}")
    tasks = episode.get("tasks", [])
    if len(tasks) <= 1:
        raise ValueError("至少保留一个任务")
    episode["tasks"] = [task for task in tasks if task.get("lane_id") != lane_id]
    if not episode["tasks"]:
        raise ValueError("任务不存在")
    assign_output_slugs_for_episode(episode)
