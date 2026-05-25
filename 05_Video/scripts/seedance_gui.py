#!/usr/bin/env python3
"""Small local GUI for the Seedance batch runner."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from media_refs import (
    MAX_IMAGE_BYTES,
    MAX_REFERENCE_IMAGES,
    MAX_REFERENCE_VIDEOS,
    MAX_VIDEO_BYTES,
    media_kind,
)
from concat_episode import concat_episode_previews
from export_versions import export_version_label, latest_export_rel_path, max_export_version
from segment_config import MAX_DURATION, MIN_DURATION, get_segment_mode
from workspace_store import (
    all_lane_ids,
    create_episode_entry,
    default_episode,
    default_task,
    default_workspace,
    episode_lane_ids,
    find_episode,
    load_workspace,
    next_episode_id,
    next_task_id,
    output_slug_for_lane,
    remove_episode,
    remove_task,
    save_workspace,
    suggest_episode_no,
    update_episode_meta,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
CONFIG = ROOT / "05_Video" / "seedance_batch.json"
UPLOAD_DIR = ROOT / "05_Video" / "uploads"
EXPORT_DIR = ROOT / "05_Video" / "exports"
SEGMENT_DIR = ROOT / "05_Video" / "segments"
HOST = "127.0.0.1"
PORT = 8765
GUI_API_VERSION = 10
BUILD_ID = "20260522-prompt-optimizer"
MAX_JOB_LOG_LINES = 400
EXIT_NO_NEW_VIDEO = 2
PROMPT_OPTIMIZER_BASE_URL = os.environ.get("PROMPT_OPTIMIZER_BASE_URL", "http://127.0.0.1:8000/v1")
PROMPT_OPTIMIZER_MODEL = os.environ.get("PROMPT_OPTIMIZER_MODEL", "deepseek-chat")
PROMPT_OPTIMIZER_API_KEY = os.environ.get("PROMPT_OPTIMIZER_API_KEY", "")
PROMPT_OPTIMIZER_TIMEOUT = int(os.environ.get("PROMPT_OPTIMIZER_TIMEOUT", "90"))

job_lock = threading.Lock()
workspace_lock = threading.Lock()
workspace_state: dict = default_workspace()
DEFAULT_DURATION = 4
DEFAULT_TEMPLATE_ASSET = "GUI_QUICK"
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


def empty_job() -> dict:
    return {
        "running": False,
        "mode": "",
        "asset": "",
        "platform": "TikTok",
        "returncode": None,
        "phase": "idle",
        "status_text": "就绪",
        "preview_url": "",
        "api_submitted": False,
        "log_lines": [],
    }


jobs: dict[str, dict] = {}


def ensure_lane(lane_id: str) -> None:
    with job_lock:
        if lane_id not in jobs:
            jobs[lane_id] = empty_job()


def lane_is_running(lane_id: str) -> bool:
    with job_lock:
        job = jobs.get(lane_id)
        return bool(job and job.get("running"))


def episode_has_running_task(episode_id: str) -> str | None:
    episode = find_episode(get_workspace(), episode_id)
    if not episode:
        return None
    for lane_id in episode_lane_ids(episode):
        if lane_is_running(lane_id):
            return lane_id
    return None


def remove_lane_job(lane_id: str) -> None:
    with job_lock:
        jobs.pop(lane_id, None)


def reset_lane_job(lane_id: str, message: str = "就绪") -> None:
    with job_lock:
        jobs[lane_id] = empty_job()
        jobs[lane_id]["status_text"] = message
        jobs[lane_id]["phase"] = "idle"


def clear_stuck_running_jobs() -> None:
    with job_lock:
        for lane_id, job in list(jobs.items()):
            if job.get("running"):
                reset_lane_job(lane_id, "已重置（上次任务异常中断）")


def sync_jobs_from_workspace(workspace: dict) -> None:
    with job_lock:
        for lane_id in all_lane_ids(workspace):
            if lane_id not in jobs:
                jobs[lane_id] = empty_job()


def init_workspace() -> None:
    global workspace_state
    with workspace_lock:
        workspace_state = load_workspace()
    sync_jobs_from_workspace(workspace_state)
    clear_stuck_running_jobs()


def get_workspace() -> dict:
    with workspace_lock:
        return json.loads(json.dumps(workspace_state))


def update_workspace(mutator) -> dict:
    global workspace_state
    with workspace_lock:
        mutator(workspace_state)
        workspace_state = save_workspace(workspace_state)
    sync_jobs_from_workspace(workspace_state)
    return get_workspace()

GUI_PAGE = SCRIPTS / "gui_page.html"


def load_gui_html() -> str:
    return GUI_PAGE.read_text(encoding="utf-8")


def prompt_optimizer_config() -> dict:
    base_url = PROMPT_OPTIMIZER_BASE_URL.rstrip("/")
    return {
        "base_url": base_url,
        "model": PROMPT_OPTIMIZER_MODEL,
        "has_api_key": bool(PROMPT_OPTIMIZER_API_KEY),
    }


def prompt_optimizer_endpoint() -> str:
    base_url = PROMPT_OPTIMIZER_BASE_URL.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def build_prompt_optimizer_messages(payload: dict) -> list[dict[str, str]]:
    prompt = str(payload.get("prompt", "")).strip()
    task_label = str(payload.get("task_label", "分镜任务")).strip() or "分镜任务"
    user_request = str(payload.get("instruction", "")).strip()
    duration = payload.get("duration", "")
    gen_mode = str(payload.get("generation_mode", "")).strip()
    reference_count = int(payload.get("reference_count") or 0)

    if not prompt:
        raise ValueError("请先填写当前 Prompt")
    if not user_request:
        user_request = "请分析这个视频生成提示词的问题，并给出更适合 Seedance 视频生成的优化版本。"

    context = [
        f"任务: {task_label}",
        f"生成方式: {gen_mode or '未填写'}",
        f"时长: {duration or '未填写'} 秒",
        f"参考素材数量: {reference_count}",
        "当前 Prompt:",
        prompt,
        "",
        "用户要求:",
        user_request,
    ]
    return [
        {
            "role": "system",
            "content": (
                "你是 Seedance 视频生成提示词优化助手。请用中文分析提示词，"
                "重点检查主体、场景、动作、镜头、节奏、风格、时长匹配、参考图一致性。"
                "输出必须包含两个小节：【问题分析】和【优化提示词】。"
                "【优化提示词】中只写可直接粘贴到视频生成器的最终提示词，"
                "并保留或补充：人物形象和场景严格参考参考图，杜绝美化和修改。"
            ),
        },
        {"role": "user", "content": "\n".join(context)},
    ]


def call_prompt_optimizer(payload: dict) -> dict:
    endpoint = prompt_optimizer_endpoint()
    body = {
        "model": PROMPT_OPTIMIZER_MODEL,
        "messages": build_prompt_optimizer_messages(payload),
        "stream": False,
        "temperature": 0.4,
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if PROMPT_OPTIMIZER_API_KEY:
        headers["Authorization"] = f"Bearer {PROMPT_OPTIMIZER_API_KEY}"

    req = urllib.request.Request(endpoint, data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=PROMPT_OPTIMIZER_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"提示词代理 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"无法连接提示词代理 {endpoint}。请先启动 deepseek-free-api/deepseek2api/ds2api。"
        ) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"提示词代理返回的不是 JSON: {text[:300]}") from exc

    content = ""
    choices = data.get("choices") if isinstance(data, dict) else None
    if choices:
        first = choices[0] or {}
        message = first.get("message") or {}
        content = str(message.get("content") or first.get("text") or "").strip()
    if not content and isinstance(data, dict):
        content = str(data.get("output_text") or data.get("content") or "").strip()
    if not content:
        raise RuntimeError("提示词代理没有返回可用内容")
    model = data.get("model") if isinstance(data, dict) else ""
    return {"content": content, "model": model or PROMPT_OPTIMIZER_MODEL}



def resolve_upload_path(rel_path: str) -> Path:
    target = (ROOT / rel_path).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if not str(target).startswith(str(upload_root)):
        raise ValueError("只能操作 uploads 目录内的文件")
    return target


def resolve_upload_media_path(rel_path: str) -> Path:
    target = resolve_upload_path(rel_path)
    if target.suffix.lower() not in MEDIA_TYPES:
        raise ValueError("不支持的媒体类型")
    return target


def delete_upload(rel_path: str) -> None:
    target = resolve_upload_path(rel_path)
    if target.exists() and target.is_file():
        target.unlink()


def save_upload(filename: str, data_base64: str) -> dict[str, str]:
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("文件数据无效") from exc
    kind = media_kind(filename)
    limit = MAX_VIDEO_BYTES if kind == "video" else MAX_IMAGE_BYTES
    if len(raw) > limit:
        label = "视频" if kind == "video" else "图片"
        raise ValueError(f"{label}不能超过 {limit // (1024 * 1024)}MB")
    suffix = Path(filename).suffix.lower()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^\w.\-]", "_", Path(filename).stem)[:40] or "upload"
    out = UPLOAD_DIR / f"{stamp}_{safe}{suffix}"
    out.write_bytes(raw)
    rel = str(out.relative_to(ROOT)).replace("\\", "/")
    return {"path": rel, "kind": kind}


def get_asset_platform(asset_id: str) -> str:
    meta = load_config_meta()
    return str(meta["assets"].get(asset_id, {}).get("platform", "TikTok"))


def export_rel_path(output_slug: str, platform: str) -> str | None:
    return latest_export_rel_path(output_slug, platform)


def resolve_media_path(rel_path: str) -> Path:
    target = (ROOT / rel_path).resolve()
    allowed = (EXPORT_DIR.resolve(), SEGMENT_DIR.resolve())
    if not any(str(target).startswith(str(root)) for root in allowed):
        raise ValueError("不允许访问该路径")
    if not target.is_file() or target.suffix.lower() != ".mp4":
        raise ValueError("视频不存在")
    return target


def preview_url_for_rel(rel_path: str) -> str:
    path = ROOT / rel_path
    stamp = int(path.stat().st_mtime) if path.is_file() else 0
    return f"/api/video?path={urllib.parse.quote(rel_path, safe='/')}&t={stamp}"


def public_job(lane_id: str, job: dict) -> dict:
    preview_url = job.get("preview_url", "")
    slug = output_slug_for_lane(get_workspace(), lane_id)
    platform = job.get("platform", "TikTok")
    if job.get("asset"):
        platform = get_asset_platform(job["asset"])
    if not preview_url:
        rel = export_rel_path(slug, platform)
        if rel and (ROOT / rel).is_file():
            preview_url = preview_url_for_rel(rel)
    output_label = export_version_label(slug, platform) if max_export_version(slug, platform) > 0 else ""

    phase = job.get("phase", "idle")
    api_sent = bool(job.get("api_submitted"))
    if phase == "success":
        video_status = "success"
    elif phase == "failed":
        video_status = "failed"
    elif job.get("running") and api_sent:
        video_status = "generating"
    elif job.get("running"):
        video_status = "pending"
    else:
        video_status = "idle"

    return {
        "running": job["running"],
        "mode": job.get("mode", ""),
        "asset": job.get("asset", ""),
        "returncode": job["returncode"],
        "phase": phase,
        "status_text": job.get("status_text", "就绪"),
        "preview_url": preview_url,
        "output_label": output_label,
        "api_sent": api_sent,
        "video_status": video_status,
        "log": "\n".join(job.get("log_lines", [])),
    }


def set_preview(lane: str, rel_path: str) -> None:
    jobs[lane]["preview_url"] = preview_url_for_rel(rel_path)


def update_job_status(lane: str, *, phase: str | None = None, status_text: str | None = None) -> None:
    if phase is not None:
        jobs[lane]["phase"] = phase
    if status_text is not None:
        jobs[lane]["status_text"] = status_text
        if jobs[lane].get("running"):
            append_job_log(lane, f"[状态] {status_text}")


def append_job_log(lane: str, line: str) -> None:
    text = line.rstrip("\r\n")
    if not text.strip():
        return
    if text.startswith("ffmpeg version") or "built with gcc" in text:
        return
    lines = jobs[lane].setdefault("log_lines", [])
    lines.append(text)
    if len(lines) > MAX_JOB_LOG_LINES:
        del lines[: len(lines) - MAX_JOB_LOG_LINES]


def clear_job_log(lane: str) -> None:
    jobs[lane]["log_lines"] = []


def _generate_success_label(rel_path: str) -> str:
    return f"生成成功 ({Path(rel_path).name.replace('.mp4', '')})"


def parse_runner_line(lane: str, line: str, mode: str) -> None:
    text = line.strip()
    if not text:
        return
    if mode == "generate" and "GUI_GENERATE:" in text:
        return
    if mode == "generate" and "API_REQUEST: encoding " in text:
        part = text.split("API_REQUEST: encoding", 1)[-1].strip()
        update_job_status(lane, phase="running", status_text=f"API：编码参考图 {part}｜视频：等待")
        return
    if mode == "generate" and (
        "compress_image" in text or "base64_encode" in text or "encoding_media" in text
    ):
        update_job_status(lane, phase="running", status_text="API：编码参考图中…｜视频：等待")
        return
    if mode == "generate" and "API_REQUEST: POST" in text:
        update_job_status(lane, phase="running", status_text="API：正在提交…｜视频：等待")
        return
    if mode == "generate" and text.startswith("Mode "):
        update_job_status(lane, phase="running", status_text="API：准备中…｜视频：等待")
        return
    if "Submitted " in text:
        jobs[lane]["api_submitted"] = True
        if mode == "generate":
            update_job_status(lane, phase="running", status_text="API：已发送｜视频：生成中")
        else:
            update_job_status(lane, phase="running", status_text="生成中...")
        return
    if " status: " in text:
        state = text.rsplit(" status: ", 1)[-1].strip().lower()
        if mode == "generate":
            if state in {"running", "processing", "pending", "queued", "in_progress"}:
                update_job_status(lane, phase="running", status_text="API：已发送｜视频：生成中")
            elif state in {"succeeded", "success", "completed"}:
                update_job_status(lane, phase="running", status_text="API：已发送｜视频：下载中")
            elif state in {"failed", "error", "canceled", "cancelled"}:
                update_job_status(lane, phase="failed", status_text="API：已发送｜视频：失败")
            return
        if state in {"running", "processing", "pending", "queued", "in_progress"}:
            update_job_status(lane, phase="running", status_text="生成中...")
        elif state in {"succeeded", "success", "completed"}:
            update_job_status(lane, phase="running", status_text="生成完成，正在下载...")
        elif state in {"failed", "error", "canceled", "cancelled"}:
            update_job_status(lane, phase="failed", status_text="生成失败")
        return
    if text.startswith("Saved:"):
        if mode == "generate":
            update_job_status(lane, phase="running", status_text="API：已发送｜视频：合成中")
        else:
            rel = text.split(":", 1)[1].strip()
            set_preview(lane, rel)
            update_job_status(lane, phase="running", status_text="生成中...")
        return
    if text.startswith("Exported:"):
        rel = text.split(":", 1)[1].strip()
        set_preview(lane, rel)
        if mode == "generate":
            update_job_status(lane, phase="success", status_text=f"API：已发送｜视频：成功（{Path(rel).name.replace('.mp4', '')}）")
        else:
            update_job_status(lane, phase="running", status_text="生成中...")
        return
    if text.startswith("Fresh:"):
        if mode == "generate":
            update_job_status(lane, phase="failed", status_text="生成失败：未重新请求 API（请重启 GUI）")
        return
    if "RESULT: no_new_video" in text:
        if mode == "generate":
            update_job_status(
                lane,
                phase="failed",
                status_text="生成失败：服务未强制重新生成，请重启 start_seedance_gui.bat",
            )
        return
    if "RESULT: force_generate_failed" in text:
        update_job_status(lane, phase="failed", status_text="生成失败：API 未返回新片段")
        return
    if mode == "dry" and (text.startswith("Dry-run ") or text.startswith("Mode ")):
        update_job_status(lane, phase="running", status_text="快速检查中...")
    if mode == "dry" and text.startswith("Dry-run OK"):
        update_job_status(lane, phase="running", status_text="检查通过")


def finalize_job(lane: str, code: int | None, mode: str) -> None:
    job = jobs[lane]
    run_mode = str(job.get("mode") or mode or "").strip()
    platform = job.get("platform", "TikTok")
    if run_mode == "dry":
        if code == 0:
            update_job_status(lane, phase="success", status_text="检查通过")
        else:
            update_job_status(lane, phase="failed", status_text="检查失败")
        return
    if run_mode == "generate":
        if job.get("phase") == "success":
            return
        slug = output_slug_for_lane(get_workspace(), lane)
        rel = export_rel_path(slug, platform)
        if code == 0 and rel and (ROOT / rel).is_file():
            if not job.get("preview_url"):
                set_preview(lane, rel)
            update_job_status(lane, phase="success", status_text=_generate_success_label(rel))
            return
        if job.get("phase") == "failed":
            return
        if code == EXIT_NO_NEW_VIDEO or not job.get("api_submitted"):
            update_job_status(lane, phase="failed", status_text="API：未发送｜视频：未生成（请重启 start_seedance_gui.bat）")
        else:
            update_job_status(lane, phase="failed", status_text="API：已发送｜视频：失败")
        return
    if code == EXIT_NO_NEW_VIDEO:
        update_job_status(lane, phase="failed", status_text="API：未发送｜视频：未生成")
        return
    if code == 0:
        if not job.get("preview_url"):
            slug = output_slug_for_lane(get_workspace(), lane)
            rel = export_rel_path(slug, platform)
            if rel and (ROOT / rel).is_file():
                set_preview(lane, rel)
                update_job_status(lane, phase="success", status_text=_generate_success_label(rel))
                return
        update_job_status(lane, phase="success", status_text="生成成功")
        return
    if job.get("phase") != "failed":
        update_job_status(lane, phase="failed", status_text="生成失败")


def load_config_meta() -> dict:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    assets: dict = {}
    for asset_id, asset in data.get("assets", {}).items():
        assets[asset_id] = {
            "platform": asset.get("platform", "TikTok"),
            "segments": [
                {
                    "id": segment.get("id", "?"),
                    "mode": get_segment_mode(segment),
                    "duration": segment.get("duration"),
                    "prompt": segment.get("prompt", ""),
                }
                for segment in asset.get("segments", [])
            ],
        }
    return {"defaults": data.get("defaults", {}), "assets": assets}


def runner_python_executable() -> str:
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        candidate = Path(exe).with_name("python.exe")
        if candidate.is_file():
            return str(candidate)
    return exe


def run_command(
    lane: str,
    mode: str,
    asset: str,
    duration: int | None = None,
    prompt: str | None = None,
    generation_mode: str = "asset",
    references: list[str] | None = None,
    start_frame: str = "",
    end_frame: str = "",
) -> None:
    if lane not in jobs:
        return
    platform = get_asset_platform(asset)
    with job_lock:
        if jobs[lane]["running"]:
            append_job_log(lane, "[错误] 任务已在运行，跳过重复启动")
            return
        jobs[lane].update(
            {
                "running": True,
                "mode": mode,
                "asset": asset,
                "platform": platform,
                "returncode": None,
                "phase": "running",
                "status_text": "快速检查中..." if mode == "dry" else jobs[lane].get("status_text", "正在启动..."),
                "api_submitted": False,
            }
        )
        append_job_log(lane, ">>> 子进程启动中…")

    runner = str(SCRIPTS / "run_seedance_batch.py")
    cmd: list[str] = []
    py = [runner_python_executable(), "-u"]
    if mode == "dry":
        cmd = [*py, runner, "--asset", asset, "--dry-run"]
    elif mode == "generate":
        cmd = [*py, runner, "--asset", asset, "--gui-generate", "--force"]
    elif mode == "all":
        cmd = [*py, runner, "--all"]
    else:
        cmd = []

    if cmd:
        duration_value = DEFAULT_DURATION if duration is None else int(duration)
        clamped = max(MIN_DURATION, min(MAX_DURATION, duration_value))
        cmd.extend(["--duration", str(clamped)])
        output_id = output_slug_for_lane(get_workspace(), lane)
        cmd.extend(["--output-id", output_id])

    if cmd and prompt and prompt.strip():
        cmd.extend(["--prompt", prompt.strip()])

    if cmd and generation_mode in {"text", "reference", "keyframes", "asset"}:
        cmd.extend(["--generation-mode", generation_mode])

    if cmd and references:
        for ref in references:
            cmd.extend(["--reference", ref])

    if cmd and start_frame.strip():
        cmd.extend(["--start-frame", start_frame.strip()])
    if cmd and end_frame.strip():
        cmd.extend(["--end-frame", end_frame.strip()])

    if not cmd:
        with job_lock:
            update_job_status(lane, phase="failed", status_text="未知任务类型")
            jobs[lane].update({"running": False, "returncode": 2})
        return

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if mode == "generate":
        env["SEEDANCE_GUI_FORCE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
    with job_lock:
        append_job_log(lane, "$ " + " ".join(cmd))
        append_job_log(lane, "[提示] 若长时间无新日志，可能在压缩/编码参考图（大图约 1–3 分钟）")

    def _heartbeat() -> None:
        while proc.poll() is None:
            time.sleep(10)
            with job_lock:
                if jobs.get(lane, {}).get("running"):
                    append_job_log(lane, f"[等待] 仍在处理… {time.strftime('%H:%M:%S')}")

    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0,
    )
    threading.Thread(target=_heartbeat, daemon=True).start()
    code = -1
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            with job_lock:
                append_job_log(lane, line)
                parse_runner_line(lane, line, mode)
        code = proc.wait()
    except Exception as exc:
        with job_lock:
            append_job_log(lane, f"任务异常: {exc}")
            update_job_status(lane, phase="failed", status_text=f"任务异常: {exc}")
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        code = proc.returncode if proc.returncode is not None else 1
    finally:
        with job_lock:
            append_job_log(lane, f"<<< 结束 exit={code}")
            jobs[lane].update({"running": False, "returncode": code})
            finalize_job(lane, code, mode)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = load_gui_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/ping":
            self.send_json(
                {
                    "ok": True,
                    "api_version": GUI_API_VERSION,
                    "build_id": BUILD_ID,
                    "features": [
                        "workspace",
                        "episodes",
                        "delete",
                        "media",
                        "force_regenerate",
                        "versioned_export",
                        "episode_concat",
                        "prompt_optimizer",
                    ],
                    "prompt_optimizer": prompt_optimizer_config(),
                }
            )
            return
        if parsed.path == "/api/prompt/config":
            self.send_json({"ok": True, **prompt_optimizer_config()})
            return
        if parsed.path in {"/api/assets", "/api/config"}:
            meta = load_config_meta()
            if parsed.path == "/api/assets":
                self.send_json({"assets": list(meta["assets"].keys())})
            else:
                self.send_json(meta)
            return
        if parsed.path == "/api/workspace":
            ws = get_workspace()
            with job_lock:
                job_payload = {
                    lane_id: public_job(lane_id, jobs[lane_id])
                    for lane_id in all_lane_ids(ws)
                    if lane_id in jobs
                }
            self.send_json({"ok": True, "workspace": ws, "jobs": job_payload})
            return
        if parsed.path in {"/api/job", "/api/jobs"}:
            ws = get_workspace()
            with job_lock:
                payload = {
                    lane_id: public_job(lane_id, jobs[lane_id])
                    for lane_id in all_lane_ids(ws)
                    if lane_id in jobs
                }
            self.send_json(payload)
            return
        if parsed.path == "/api/media":
            query = urllib.parse.parse_qs(parsed.query)
            rel_path = (query.get("path") or [""])[0]
            try:
                target = resolve_upload_media_path(rel_path)
            except ValueError:
                self.send_error(404)
                return
            data = target.read_bytes()
            mime = MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/video":
            query = urllib.parse.parse_qs(parsed.query)
            rel_path = (query.get("path") or [""])[0]
            try:
                target = resolve_media_path(rel_path)
            except ValueError as exc:
                self.send_error(404, str(exc))
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def handle_upload(self) -> None:
        try:
            payload = self.read_json_body()
            saved = save_upload(str(payload.get("filename", "upload.png")), str(payload.get("data", "")))
            self.send_json({"ok": True, **saved})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def handle_upload_delete(self) -> None:
        try:
            payload = self.read_json_body()
            delete_upload(str(payload.get("path", "")))
            self.send_json({"ok": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def handle_save_workspace(self) -> None:
        try:
            payload = self.read_json_body()
            incoming = payload.get("workspace")
            if not isinstance(incoming, dict):
                raise ValueError("workspace 格式无效")

            def mutate(ws: dict) -> None:
                saved = save_workspace(incoming)
                ws.clear()
                ws.update(saved)

            saved = update_workspace(mutate)
            with job_lock:
                job_payload = {
                    lane_id: public_job(lane_id, jobs[lane_id])
                    for lane_id in all_lane_ids(saved)
                    if lane_id in jobs
                }
            self.send_json({"ok": True, "workspace": saved, "jobs": job_payload})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def handle_create_episode(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.read_json_body() if length else {}
            raw_no = payload.get("episode_no")
            if raw_no is None:
                episode_no = suggest_episode_no(get_workspace())
            else:
                episode_no = int(raw_no)
            title = str(payload.get("title", "")).strip() or None
            if episode_no < 1:
                raise ValueError("集数须为大于 0 的整数")

            def mutate(ws: dict) -> None:
                create_episode_entry(ws, episode_no, title)

            ws = update_workspace(mutate)
            episode = ws["episodes"][-1]
            self.send_json({"ok": True, "episode": episode, "workspace": ws})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def handle_update_episode(self) -> None:
        try:
            payload = self.read_json_body()
            episode_id = str(payload.get("episode_id", "")).strip()
            if not episode_id:
                raise ValueError("缺少 episode_id")
            episode_no_raw = payload.get("episode_no")
            episode_no = int(episode_no_raw) if episode_no_raw is not None else None
            title = payload.get("title")
            if title is not None:
                title = str(title).strip()

            def mutate(ws: dict) -> None:
                update_episode_meta(ws, episode_id, episode_no=episode_no, title=title)

            ws = update_workspace(mutate)
            episode = find_episode(ws, episode_id)
            self.send_json({"ok": True, "episode": episode, "workspace": ws})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def handle_create_lane(self) -> None:
        episode_id = ""
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            try:
                payload = self.read_json_body()
                episode_id = str(payload.get("episode_id", "")).strip()
            except json.JSONDecodeError:
                self.send_json({"ok": False, "error": "请求 JSON 无效"}, status=400)
                return

        def mutate(ws: dict) -> None:
            nonlocal episode_id
            if not episode_id:
                if not ws["episodes"]:
                    episode_id = next_episode_id(ws)
                    ws["episodes"].append(default_episode(episode_id, f"第 {ws['episode_seq']} 集"))
                episode_id = ws["episodes"][-1]["id"]
            episode = find_episode(ws, episode_id)
            if not episode:
                raise ValueError(f"未知集数: {episode_id}")
            lane_id = next_task_id(episode)
            episode["tasks"].append(default_task(lane_id))

        try:
            ws = update_workspace(mutate)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        episode = find_episode(ws, episode_id)
        lane_id = episode["tasks"][-1]["lane_id"] if episode else ""
        self.send_json({"ok": True, "lane": lane_id, "episode_id": episode_id, "task": episode["tasks"][-1]})

    def handle_concat_episode(self) -> None:
        try:
            payload = self.read_json_body()
            episode_id = str(payload.get("episode_id", "")).strip()
            if not episode_id:
                raise ValueError("缺少 episode_id")
            episode = find_episode(get_workspace(), episode_id)
            if not episode:
                raise ValueError(f"未知集数: {episode_id}")
            tasks = episode.get("tasks") or []
            platform = (
                get_asset_platform(str(tasks[0].get("asset", DEFAULT_TEMPLATE_ASSET)))
                if tasks
                else "TikTok"
            )
            output, included, skipped = concat_episode_previews(episode, platform)
            rel = str(output.relative_to(ROOT)).replace("\\", "/")
            self.send_json(
                {
                    "ok": True,
                    "path": rel,
                    "preview_url": preview_url_for_rel(rel),
                    "filename": output.name,
                    "included": included,
                    "skipped": skipped,
                    "message": f"已拼接 {len(included)} 个分镜"
                    + (f"，跳过 {len(skipped)} 个无预览" if skipped else ""),
                }
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            self.send_json({"ok": False, "error": f"ffmpeg 拼接失败: {detail}"}, status=500)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def handle_prompt_optimize(self) -> None:
        try:
            payload = self.read_json_body()
            result = call_prompt_optimizer(payload)
            self.send_json({"ok": True, **result, "config": prompt_optimizer_config()})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=502)

    def handle_delete_episode(self) -> None:
        try:
            payload = self.read_json_body()
            episode_id = str(payload.get("episode_id", "")).strip()
            if not episode_id:
                raise ValueError("缺少 episode_id")
            running = episode_has_running_task(episode_id)
            if running:
                self.send_json({"ok": False, "error": f"{running} 正在生成中，无法删除"}, status=409)
                return
            episode = find_episode(get_workspace(), episode_id)
            if not episode:
                raise ValueError(f"未知集数: {episode_id}")
            lane_ids_to_remove = episode_lane_ids(episode)

            def mutate(ws: dict) -> None:
                remove_episode(ws, episode_id)

            ws = update_workspace(mutate)
            for lane_id in lane_ids_to_remove:
                remove_lane_job(lane_id)
            self.send_json({"ok": True, "workspace": ws})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def handle_delete_task(self) -> None:
        try:
            payload = self.read_json_body()
            episode_id = str(payload.get("episode_id", "")).strip()
            lane_id = str(payload.get("lane_id", "")).strip()
            if not episode_id or not lane_id:
                raise ValueError("缺少 episode_id 或 lane_id")
            if lane_is_running(lane_id):
                self.send_json({"ok": False, "error": f"{lane_id} 正在生成中，无法删除"}, status=409)
                return

            def mutate(ws: dict) -> None:
                remove_task(ws, episode_id, lane_id)

            ws = update_workspace(mutate)
            remove_lane_job(lane_id)
            self.send_json({"ok": True, "workspace": ws})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def handle_reset_lane(self) -> None:
        try:
            payload = self.read_json_body() if int(self.headers.get("Content-Length", "0")) else {}
            lane = str(payload.get("lane", "")).strip()
            if not lane:
                raise ValueError("缺少 lane")
            ensure_lane(lane)
            reset_lane_job(lane)
            self.send_json({"ok": True, "lane": lane})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def handle_run(self) -> None:
        payload = self.read_json_body()
        lane = str(payload.get("lane", "")).strip()
        ensure_lane(lane)
        if lane not in jobs:
            self.send_json({"ok": False, "error": f"未知任务: {lane}"}, status=400)
            return
        with job_lock:
            if jobs[lane]["running"]:
                self.send_json(
                    {"ok": False, "error": f"{lane} 正在运行中，请先刷新页面或重置任务"},
                    status=409,
                )
                return
        mode = str(payload.get("mode", "")).strip()
        asset = payload.get("asset", "")
        duration = payload.get("duration")
        prompt = payload.get("prompt", "")
        generation_mode = payload.get("generation_mode", "asset")
        references = payload.get("references") or []
        if not isinstance(references, list):
            references = []
        start_frame = str(payload.get("start_frame", ""))
        end_frame = str(payload.get("end_frame", ""))
        if mode == "generate":
            platform = get_asset_platform(asset)
            with job_lock:
                clear_job_log(lane)
                append_job_log(lane, f">>> 正式生成开始 {time.strftime('%H:%M:%S')}")
                jobs[lane].update(
                    {
                        "mode": "generate",
                        "asset": asset,
                        "platform": platform,
                        "phase": "running",
                        "status_text": "API：准备编码参考图…｜视频：等待",
                        "api_submitted": False,
                        "preview_url": jobs[lane].get("preview_url", ""),
                        "running": False,
                    }
                )
                append_job_log(lane, "[状态] API：准备编码参考图…｜视频：等待")
        elif mode == "dry":
            with job_lock:
                clear_job_log(lane)
                append_job_log(lane, f">>> 快速检查开始 {time.strftime('%H:%M:%S')}")
                jobs[lane]["running"] = False
        thread = threading.Thread(
            target=run_command,
            args=(lane, mode, asset, duration, prompt, generation_mode, references, start_frame, end_frame),
            daemon=True,
        )
        thread.start()
        self.send_json({"ok": True, "lane": lane})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/workspace":
            self.handle_save_workspace()
            return
        if parsed.path == "/api/episodes/create":
            self.handle_create_episode()
            return
        if parsed.path == "/api/episodes/update":
            self.handle_update_episode()
            return
        if parsed.path == "/api/episodes/concat":
            self.handle_concat_episode()
            return
        if parsed.path == "/api/prompt/optimize":
            self.handle_prompt_optimize()
            return
        if parsed.path == "/api/episodes/delete":
            self.handle_delete_episode()
            return
        if parsed.path == "/api/tasks/delete":
            self.handle_delete_task()
            return
        if parsed.path == "/api/lanes/create":
            self.handle_create_lane()
            return
        if parsed.path == "/api/lanes/reset":
            self.handle_reset_lane()
            return
        if parsed.path == "/api/upload":
            self.handle_upload()
            return
        if parsed.path == "/api/upload/delete":
            self.handle_upload_delete()
            return
        if parsed.path == "/api/run":
            self.handle_run()
            return
        self.send_json({"ok": False, "error": f"未知接口: {parsed.path}"}, status=404)


def main() -> None:
    init_workspace()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Open http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
