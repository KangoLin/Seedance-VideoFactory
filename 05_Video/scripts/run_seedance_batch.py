#!/usr/bin/env python3
"""Generate video segments from 05_Video/seedance_batch.json."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from export_versions import export_file_path, next_export_version
from media_refs import is_video_file
from segment_config import (
    MODE_FIRST_FRAME,
    MODE_KEYFRAMES,
    MODE_REFERENCE,
    MODE_TEXT,
    get_segment_mode,
    reference_paths,
    resolve_generation_params,
    segment_required_paths,
    validate_segment,
)

GENERATION_MODES = ("asset", "text", "reference", "keyframes")
EXIT_NO_NEW_VIDEO = 2


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEY_PATH = ROOT / "API_Key" / "VE_Key.txt"
DEFAULT_CONFIG = ROOT / "05_Video" / "seedance_batch.json"
DEFAULT_SEGMENT_DIR = ROOT / "05_Video" / "segments"
DEFAULT_EXPORT_DIR = ROOT / "05_Video" / "exports"

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
CREATE_PATH = "/contents/generations/tasks"
DEFAULT_MODEL = "doubao-seedance-2-0-fast-260128"

VERSIONED_KEYFRAME_RE = re.compile(r"^(?P<prefix>.+)_v(?P<version>\d+)\.png$")
PROMPT_SUFFIX = " Vertical mobile video, 9:16, no generated text, no subtitles, no speech bubbles."


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assets = data.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise SystemExit(f"No assets found in {path}")
    defaults = data.get("defaults", {})
    if defaults is not None and not isinstance(defaults, dict):
        raise SystemExit(f"Invalid defaults section in {path}")
    return {"defaults": defaults or {}, "assets": assets}


def resolve_keyframe(path: str | Path) -> Path:
    candidate = ROOT / path
    match = VERSIONED_KEYFRAME_RE.match(candidate.name)
    if not match:
        return candidate

    prefix = match.group("prefix")
    versions = sorted(
        candidate.parent.glob(f"{prefix}_v*.png"),
        key=lambda item: int(VERSIONED_KEYFRAME_RE.match(item.name).group("version"))
        if VERSIONED_KEYFRAME_RE.match(item.name)
        else -1,
    )
    return versions[-1] if versions else candidate


def read_key(path: Path) -> str:
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit(f"API key file is empty: {path}")
    return key


API_CACHE_DIR = DEFAULT_SEGMENT_DIR / ".api_cache"
API_IMAGE_MAX_BYTES = 1024 * 1024
_FFMPEG_EXE: str | None = None
API_VIDEO_MAX_BYTES = int(os.environ.get("REFERENCE_VIDEO_MAX_BYTES", str(50 * 1024 * 1024)))
REFERENCE_VIDEO_URL_CACHE = API_CACHE_DIR / "reference_video_urls.json"
REFERENCE_VIDEO_URL_CACHE_SECONDS = int(os.environ.get("REFERENCE_VIDEO_URL_CACHE_SECONDS", "1800"))


def is_web_url(value: str | Path) -> bool:
    parsed = urllib.parse.urlparse(str(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def web_url_suffix(value: str) -> str:
    return Path(urllib.parse.urlparse(value).path).suffix.lower()


def reference_is_video(value: str | Path) -> bool:
    if is_web_url(value):
        return web_url_suffix(str(value)) in {".mp4", ".mov", ".webm"}
    return is_video_file(value)


def ensure_api_sized_media(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if is_video_file(path):
        if size > API_VIDEO_MAX_BYTES:
            raise ValueError(
                f"参考视频 {path.name} 过大 ({size // (1024 * 1024)}MB)，"
                f"请压缩到 {API_VIDEO_MAX_BYTES // (1024 * 1024)}MB 以内再生成"
            )
        return path
    if size <= API_IMAGE_MAX_BYTES:
        return path
    API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = API_CACHE_DIR / f"{path.stem}_{size // 1024}k_api.jpg"
    if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
        print(
            f"API_REQUEST: use_cached {path.name} -> {out.name} ({out.stat().st_size // 1024}KB)",
            flush=True,
        )
        return out
    print(f"API_REQUEST: compress_image {path.name} ({size // 1024}KB)...", flush=True)
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-y",
            "-i",
            str(path),
            "-vf",
            "scale='min(1280,iw)':-2",
            "-q:v",
            "5",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    print(f"API_REQUEST: compressed -> {out.name} ({out.stat().st_size // 1024}KB)", flush=True)
    return out


def media_data_url(path: Path) -> str:
    path = ensure_api_sized_media(path)
    if is_video_file(path):
        mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    else:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    size_kb = path.stat().st_size // 1024
    print(f"API_REQUEST: base64_encode {path.name} ({size_kb}KB) 开始…", flush=True)
    raw = path.read_bytes()
    print(f"API_REQUEST: base64_encode {path.name} 读取完成，正在编码…", flush=True)
    encoded = base64.b64encode(raw).decode("ascii")
    print(f"API_REQUEST: base64_encode {path.name} 完成 ({len(encoded) // 1024}KB)", flush=True)
    return f"data:{mime};base64,{encoded}"


def media_ref_for_dry_run(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    kind = "video" if is_video_file(path) else "image"
    size_mb = path.stat().st_size / (1024 * 1024)
    return f"dry-run://{path.name} ({kind}, {size_mb:.2f} MB)"


def load_reference_video_url_cache() -> dict:
    if not REFERENCE_VIDEO_URL_CACHE.is_file():
        return {}
    try:
        return json.loads(REFERENCE_VIDEO_URL_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_reference_video_url_cache(cache: dict) -> None:
    API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE_VIDEO_URL_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def tmpfiles_download_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("tmpfiles.org") and not parsed.path.startswith("/dl/"):
        return urllib.parse.urlunparse(parsed._replace(path="/dl" + parsed.path))
    return url


def multipart_upload(
    endpoint: str,
    path: Path,
    *,
    filename: str,
    mime: str,
    accept: str = "*/*",
) -> tuple[int, str]:
    boundary = "----seedance-reference-video-" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + path.read_bytes() + tail
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "Accept": accept,
            "User-Agent": "Mozilla/5.0 SeedanceVideoToolkit/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return int(resp.status), resp.read().decode("utf-8", errors="replace")


def try_upload_reference_video_tmpfiles(path: Path, safe_name: str, mime: str) -> str:
    _, text = multipart_upload(
        "https://tmpfiles.org/api/v1/upload",
        path,
        filename=safe_name,
        mime=mime,
        accept="application/json",
    )
    payload = json.loads(text)
    url = ((payload.get("data") or {}).get("url") if isinstance(payload, dict) else "") or ""
    if not url:
        raise RuntimeError(f"tmpfiles.org 返回异常 {payload!r}")
    return tmpfiles_download_url(str(url))


def try_upload_reference_video_0x0(path: Path, safe_name: str, mime: str) -> str:
    _, text = multipart_upload("https://0x0.st", path, filename=safe_name, mime=mime, accept="text/plain")
    url = text.strip()
    if not url.startswith(("http://", "https://")):
        raise RuntimeError(f"0x0.st 返回异常 {url[:200]!r}")
    return url


def upload_reference_video_to_public_url(path: Path) -> str:
    path = ensure_api_sized_media(path)
    stat = path.stat()
    cache_key = str(path.resolve())
    cache = load_reference_video_url_cache()
    cached = cache.get(cache_key) or {}
    now = time.time()
    if (
        cached.get("size") == stat.st_size
        and cached.get("mtime") == stat.st_mtime
        and cached.get("url")
        and now - float(cached.get("created_at") or 0) < REFERENCE_VIDEO_URL_CACHE_SECONDS
    ):
        print(f"API_REQUEST: use_public_video_url_cache {path.name}", flush=True)
        return str(cached["url"])

    safe_name = re.sub(r"[^\w.\-]+", "_", path.name) or f"reference_{uuid.uuid4().hex}.mp4"
    mime = mimetypes.guess_type(safe_name)[0] or "video/mp4"
    attempts = [
        ("tmpfiles.org", try_upload_reference_video_tmpfiles),
        ("0x0.st", try_upload_reference_video_0x0),
    ]
    errors: list[str] = []
    url = ""
    for name, uploader in attempts:
        print(f"API_REQUEST: upload_reference_video {path.name} -> {name}", flush=True)
        try:
            url = uploader(path, safe_name, mime)
            break
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print(f"API_REQUEST: upload_reference_video_failed {name}: {exc}", flush=True)
    if not url:
        raise RuntimeError("参考视频公网化失败：" + "；".join(errors))
    cache[cache_key] = {
        "url": url,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "created_at": now,
    }
    save_reference_video_url_cache(cache)
    print(f"API_REQUEST: reference_video_url {url}", flush=True)
    return url


def request_json(method: str, url: str, api_key: str, payload: dict | None = None, timeout: int = 60) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}\n{detail}") from exc


def build_text_content(segment: dict) -> list[dict]:
    return [
        {
            "type": "text",
            "text": segment["prompt"] + PROMPT_SUFFIX,
        }
    ]


def append_image(
    content: list[dict],
    rel_path: str,
    role: str | None,
    role_mode: str,
    *,
    dry_run: bool = False,
) -> None:
    path = resolve_keyframe(rel_path)
    media_url = media_ref_for_dry_run(path) if dry_run else media_data_url(path)
    item: dict = {"type": "image_url", "image_url": {"url": media_url}}
    if role and role_mode == "role":
        item["role"] = role
    content.append(item)


def append_reference_media(
    content: list[dict],
    rel_path: str,
    role_mode: str,
    *,
    dry_run: bool = False,
) -> None:
    if is_web_url(rel_path):
        media_url = rel_path
        is_video = reference_is_video(rel_path)
    else:
        path = resolve_keyframe(rel_path)
        is_video = is_video_file(path)
        if is_video:
            media_url = media_ref_for_dry_run(path) if dry_run else upload_reference_video_to_public_url(path)
        else:
            media_url = media_ref_for_dry_run(path) if dry_run else media_data_url(path)
    if is_video:
        item: dict = {"type": "video_url", "video_url": {"url": media_url}}
        role = "reference_video"
    else:
        item = {"type": "image_url", "image_url": {"url": media_url}}
        role = "reference_image"
    if role_mode == "role":
        item["role"] = role
    content.append(item)


def build_payload(
    model: str,
    segment: dict,
    params: dict,
    role_mode: str,
    *,
    dry_run: bool = False,
    show_progress: bool = False,
) -> dict:
    mode = get_segment_mode(segment)
    content = build_text_content(segment)

    if mode == MODE_REFERENCE:
        refs = reference_paths(segment)
        total = len(refs)
        for index, rel_path in enumerate(refs, start=1):
            if show_progress:
                print(
                    f"API_REQUEST: encoding {index}/{total} {Path(rel_path).name}",
                    flush=True,
                )
            append_reference_media(content, rel_path, role_mode, dry_run=dry_run)
    elif mode == MODE_FIRST_FRAME:
        rel_path = segment.get("start") or segment.get("image") or segment.get("first_frame")
        append_image(content, str(rel_path), "first_frame", role_mode, dry_run=dry_run)
    elif mode == MODE_KEYFRAMES:
        append_image(content, segment["start"], "first_frame", role_mode, dry_run=dry_run)
        append_image(content, segment["end"], "last_frame", role_mode, dry_run=dry_run)

    payload: dict = {
        "model": model,
        "content": content,
        "duration": params["duration"],
        "fps": params["fps"],
        "ratio": params["ratio"],
        "watermark": False,
    }
    if params.get("resolution"):
        payload["resolution"] = params["resolution"]
    return payload


def find_task_id(response: dict) -> str:
    for path in (("id",), ("task_id",), ("data", "id"), ("data", "task_id")):
        cur = response
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                break
            cur = cur[key]
        else:
            if isinstance(cur, str) and cur:
                return cur
    raise RuntimeError(f"Could not find task id:\n{json.dumps(response, ensure_ascii=False, indent=2)}")


def get_status(response: dict) -> str:
    for path in (("status",), ("data", "status")):
        cur = response
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                break
            cur = cur[key]
        else:
            if isinstance(cur, str):
                return cur.lower()
    return ""


def find_video_url(response: dict) -> str | None:
    stack = [response]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for value in cur.values():
                if isinstance(value, str) and value.startswith(("http://", "https://")) and ".mp4" in value:
                    return value
                stack.append(value)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def download(url: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as resp:
        output.write_bytes(resp.read())


def resolve_ffmpeg() -> str:
    global _FFMPEG_EXE
    if _FFMPEG_EXE:
        return _FFMPEG_EXE
    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG_EXE = found
        return _FFMPEG_EXE

    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        winget_packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if winget_packages.is_dir():
            matches = sorted(winget_packages.glob("Gyan.FFmpeg*/**/ffmpeg.exe"))
            if matches:
                _FFMPEG_EXE = str(matches[0])
                return _FFMPEG_EXE

    raise SystemExit(
        "未找到 ffmpeg。请安装 ffmpeg 并加入 PATH，"
        "或关闭终端 / Cursor 后重新打开再运行。"
    )


def segment_is_stale(segment: dict, output: Path, force: bool) -> bool:
    if force or not output.exists():
        return True

    input_paths = [resolve_keyframe(rel) for rel in segment_required_paths(segment) if not is_web_url(rel)]
    if not input_paths:
        return False

    newest_input = max(path.stat().st_mtime for path in input_paths)
    return newest_input > output.stat().st_mtime


def apply_prompt_override(segment: dict, prompt_override: str) -> dict:
    if not prompt_override.strip():
        return segment
    merged = dict(segment)
    merged["prompt"] = prompt_override.strip()
    return merged


def apply_duration_override(segment: dict, duration_override: int | None) -> dict:
    if duration_override is None:
        return segment
    merged = dict(segment)
    merged["duration"] = int(duration_override)
    return merged


def apply_reference_override(segment: dict, references: list[str]) -> dict:
    if not references:
        return segment
    merged = dict(segment)
    merged["mode"] = MODE_REFERENCE
    if len(references) == 1:
        merged["reference"] = references[0]
        merged.pop("references", None)
    else:
        merged["references"] = list(references)
        merged.pop("reference", None)
    for key in ("start", "end", "image", "first_frame"):
        merged.pop(key, None)
    return merged


def apply_generation_mode(segment: dict, generation_mode: str) -> dict:
    if generation_mode == "asset":
        return segment
    merged = dict(segment)
    if generation_mode == "text":
        merged["mode"] = MODE_TEXT
        for key in ("reference", "references", "start", "end", "image", "first_frame"):
            merged.pop(key, None)
    elif generation_mode == "reference":
        merged["mode"] = MODE_REFERENCE
    elif generation_mode == "keyframes":
        merged["mode"] = MODE_KEYFRAMES
    return merged


def apply_keyframes_override(segment: dict, start_frame: str, end_frame: str) -> dict:
    if not start_frame.strip() or not end_frame.strip():
        return segment
    merged = dict(segment)
    merged["mode"] = MODE_KEYFRAMES
    merged["start"] = start_frame.strip()
    merged["end"] = end_frame.strip()
    for key in ("reference", "references", "image", "first_frame"):
        merged.pop(key, None)
    return merged


def prepare_segment(segment: dict, args: argparse.Namespace) -> dict:
    segment = apply_prompt_override(segment, args.prompt)
    segment = apply_duration_override(segment, args.duration)
    segment = apply_generation_mode(segment, args.generation_mode)
    segment = apply_reference_override(segment, args.reference)
    segment = apply_keyframes_override(segment, args.start_frame, args.end_frame)
    return segment


def output_root_id(asset_id: str, output_id: str) -> str:
    cleaned = output_id.strip()
    return cleaned if cleaned else asset_id


def generate_segment(
    args: argparse.Namespace,
    defaults: dict,
    api_key: str,
    asset_id: str,
    asset: dict,
    segment: dict,
    force: bool,
) -> tuple[Path, bool]:
    print(f"SEGMENT: prepare {asset_id} {segment.get('id', '?')}", flush=True)
    segment = prepare_segment(segment, args)
    validate_segment(segment, asset_id)
    print(f"SEGMENT: validate ok {asset_id} {segment.get('id', '?')}", flush=True)
    out_id = output_root_id(asset_id, args.output_id)
    output = DEFAULT_SEGMENT_DIR / out_id / f"{out_id}_{segment['id']}.mp4"
    if force and output.exists() and not args.dry_run:
        output.unlink()
    if not force and not segment_is_stale(segment, output, force):
        print(f"Fresh: {output.relative_to(ROOT)}")
        return output, False

    cli_duration = args.duration
    if cli_duration is None:
        cli_duration = int(defaults.get("duration", 4))
    cli = {
        "duration": cli_duration,
        "fps": args.fps,
        "ratio": args.ratio,
        "resolution": args.resolution,
    }
    params = resolve_generation_params(segment, asset, defaults, cli)
    mode = get_segment_mode(segment)
    if args.dry_run:
        print(
            f"Dry-run {asset_id} {segment['id']}: {mode}, "
            f"duration={params['duration']}s, ratio={params['ratio']}（快速校验，不编码媒资）"
        )
        payload = build_payload(args.model, segment, params, args.role_mode, dry_run=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("Dry-run OK: 配置与媒资校验通过")
        return output, True

    if force:
        print("GENERATE_FORCE: 1", flush=True)
    print(
        f"Mode {asset_id} {segment['id']}: {mode}, duration={params['duration']}s, ratio={params['ratio']}",
        flush=True,
    )
    print("API_REQUEST: encoding_media（参考图压缩/编码中）", flush=True)
    payload = build_payload(
        args.model,
        segment,
        params,
        args.role_mode,
        dry_run=False,
        show_progress=True,
    )

    create_url = args.base_url.rstrip("/") + CREATE_PATH
    print("API_REQUEST: POST（正在提交 Seedance 任务）", flush=True)
    response = request_json("POST", create_url, api_key, payload)
    task_id = find_task_id(response)
    print(f"Submitted {asset_id} {segment['id']}: {task_id}", flush=True)

    status_url = f"{create_url}/{task_id}"
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        status = request_json("GET", status_url, api_key, timeout=30)
        state = get_status(status)
        print(f"{asset_id} {segment['id']} status: {state or 'unknown'}", flush=True)
        if state in {"succeeded", "success", "completed"}:
            video_url = find_video_url(status)
            if not video_url:
                raise RuntimeError(f"No video URL in completed task:\n{json.dumps(status, ensure_ascii=False, indent=2)}")
            download(video_url, output)
            print(f"Saved: {output.relative_to(ROOT)}")
            return output, True
        if state in {"failed", "error", "canceled", "cancelled"}:
            raise RuntimeError(json.dumps(status, ensure_ascii=False, indent=2))
        time.sleep(args.poll)

    raise TimeoutError(f"Timed out waiting for {asset_id} {segment['id']}")


def export_asset(asset_id: str, segment_paths: list[Path], platform: str, output_id: str = "") -> Path | None:
    if not segment_paths or not all(path.exists() for path in segment_paths):
        return None
    out_id = output_root_id(asset_id, output_id)
    concat_list = DEFAULT_SEGMENT_DIR / out_id / "concat_list.txt"
    concat_list.write_text(
        "".join(f"file '{path.name}'\n" for path in segment_paths),
        encoding="utf-8",
    )
    version = next_export_version(out_id, platform)
    output = export_file_path(out_id, platform, version)
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Export version: v{version:03d}")
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(output),
        ],
        check=True,
        cwd=concat_list.parent,
    )
    print(f"Exported: {output.relative_to(ROOT)}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seedance batch runner: keyframes / text / reference image modes."
    )
    parser.add_argument("--asset", help="Asset id to generate.")
    parser.add_argument("--all", action="store_true", help="Generate all configured assets.")
    parser.add_argument("--segment", help="Generate one segment id, for example S02.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--key-path", type=Path, default=DEFAULT_KEY_PATH)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Clip duration in seconds (4-15, or -1 for auto). GUI 传入时优先于 seedance_batch.json 片段配置。",
    )
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--resolution", default="", help="Optional: 480p / 720p / 1080p")
    parser.add_argument("--role-mode", choices=["role", "ordered"], default="role")
    parser.add_argument("--poll", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--gui-generate",
        action="store_true",
        help="GUI 正式生成：强制请求 API，导出新版本成片，不使用本地缓存跳过。",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prompt",
        default="",
        help="Override prompt text for all segments in this run (e.g. from GUI).",
    )
    parser.add_argument(
        "--generation-mode",
        choices=GENERATION_MODES,
        default="asset",
        help="GUI override: text, reference, or asset (use JSON config as-is).",
    )
    parser.add_argument(
        "--reference",
        action="append",
        default=[],
        help="Reference image path(s) relative to project root (from GUI upload).",
    )
    parser.add_argument(
        "--start-frame",
        default="",
        help="First keyframe path from GUI upload.",
    )
    parser.add_argument(
        "--end-frame",
        default="",
        help="Last keyframe path from GUI upload.",
    )
    parser.add_argument(
        "--output-id",
        default="",
        help="Override output folder/file prefix for GUI dynamic task panels.",
    )
    return parser.parse_args()


def main() -> None:
    print("RUNNER: started", flush=True)
    args = parse_args()
    if args.gui_generate or os.environ.get("SEEDANCE_GUI_FORCE") == "1":
        args.force = True
    if args.gui_generate and not args.dry_run:
        print("GUI_GENERATE: 1", flush=True)
    print("RUNNER: loading config", flush=True)
    config = load_config(args.config)
    defaults = config["defaults"]
    assets = config["assets"]
    if not args.all and not args.asset:
        raise SystemExit("Use --asset ASSET_ID or --all")

    asset_ids = list(assets) if args.all else [args.asset]
    api_key = "" if args.dry_run else read_key(args.key_path)

    for asset_id in asset_ids:
        if asset_id not in assets:
            raise SystemExit(f"Unknown asset: {asset_id}")
        asset = assets[asset_id]
        segment_paths = []
        generated_any = False
        for segment in asset["segments"]:
            if args.segment and segment["id"] != args.segment:
                continue
            path, generated = generate_segment(
                args, defaults, api_key, asset_id, asset, segment, args.force
            )
            segment_paths.append(path)
            generated_any = generated_any or generated
        if not args.dry_run and not args.segment:
            if generated_any:
                export_asset(asset_id, segment_paths, asset.get("platform", "TikTok"), args.output_id)
            elif segment_paths:
                print("Skip export: no new video from API.")
        if not args.dry_run and segment_paths and not generated_any:
            if args.gui_generate or args.force:
                print("RESULT: force_generate_failed")
                raise SystemExit(1)
            print("RESULT: no_new_video")
            raise SystemExit(EXIT_NO_NEW_VIDEO)


if __name__ == "__main__":
    main()
