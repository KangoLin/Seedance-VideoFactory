#!/usr/bin/env python3
"""启动前环境体检：Python、ffmpeg、API Key、配置文件。"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from segment_config import get_segment_mode, segment_required_paths, validate_segment


ROOT = Path(__file__).resolve().parents[2]
KEY_PATH = ROOT / "API_Key" / "VE_Key.txt"
CONFIG_PATH = ROOT / "05_Video" / "seedance_batch.json"
PLACEHOLDER_KEYS = {
    "",
    "PASTE_YOUR_VOLCENGINE_API_KEY_HERE",
    "YOUR_API_KEY_HERE",
    "REPLACE_ME",
}


def ok(message: str) -> None:
    print(f"  [OK] {message}")


def fail(message: str, hint: str = "") -> None:
    print(f"  [FAIL] {message}", file=sys.stderr)
    if hint:
        print(f"    -> {hint}", file=sys.stderr)


def resolve_ffmpeg() -> str | None:
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
    return None


def check_python() -> bool:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        fail(f"Python 版本过低：{major}.{minor}，需要 3.10 或以上", "请安装 Python 3.10+ 并确保命令行可用")
        return False
    ok(f"Python {major}.{minor}")
    return True


def check_ffmpeg() -> bool:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        fail("未找到 ffmpeg", "安装 ffmpeg 并加入 PATH。Windows 可执行：winget install Gyan.FFmpeg")
        return False
    if not shutil.which("ffprobe") and "ffmpeg.exe" in ffmpeg.lower():
        ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe"))
        if not Path(ffprobe).exists():
            fail("未找到 ffprobe", "请确认 ffmpeg 安装完整")
            return False
    elif not shutil.which("ffprobe"):
        fail("未找到 ffprobe", "通常随 ffmpeg 一起安装，请确认 ffmpeg 安装完整")
        return False
    ok("ffmpeg / ffprobe 可用")
    return True


def check_api_key() -> bool:
    if not KEY_PATH.exists():
        fail(f"缺少 API Key 文件：{KEY_PATH.relative_to(ROOT)}", "创建该文件并粘贴火山/Seedance API Key")
        return False
    key = KEY_PATH.read_text(encoding="utf-8").strip()
    if key in PLACEHOLDER_KEYS:
        fail("API Key 仍是占位符", f"编辑 {KEY_PATH.relative_to(ROOT)}，填入真实 Key")
        return False
    if len(key) < 8:
        fail("API Key 内容过短，可能未正确填写", f"检查 {KEY_PATH.relative_to(ROOT)}")
        return False
    ok(f"API Key 已配置（{KEY_PATH.relative_to(ROOT)}）")
    return True


def check_config() -> bool:
    if not CONFIG_PATH.exists():
        fail(f"缺少配置文件：{CONFIG_PATH.relative_to(ROOT)}", "确认 05_Video/seedance_batch.json 存在")
        return False
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"seedance_batch.json 不是合法 JSON：{exc}", "用编辑器修复 JSON 语法")
        return False

    assets = data.get("assets")
    if not isinstance(assets, dict) or not assets:
        fail("seedance_batch.json 里没有 assets", "至少配置一条素材")
        return False

    defaults = data.get("defaults", {})
    if defaults is not None and not isinstance(defaults, dict):
        fail("defaults 必须是对象", "检查 seedance_batch.json")
        return False

    ok(f"已加载 {len(assets)} 条素材配置")
    return True


def check_segments() -> bool:
    if not CONFIG_PATH.exists():
        return False

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assets = data.get("assets", {})
    missing: list[str] = []
    config_errors: list[str] = []

    for asset_id, asset in assets.items():
        for segment in asset.get("segments", []):
            if asset_id == "GUI_QUICK":
                continue
            try:
                validate_segment(segment, asset_id)
            except ValueError as exc:
                config_errors.append(str(exc))
                continue

            for rel in segment_required_paths(segment):
                path = ROOT / rel
                if not path.exists():
                    missing.append(f"{asset_id}/{segment.get('id', '?')} [{get_segment_mode(segment)}] -> {rel}")

    if config_errors:
        fail(f"{len(config_errors)} 个片段配置有误", "检查 seedance_batch.json 中的 mode / prompt / 图片路径")
        for item in config_errors[:6]:
            print(f"    · {item}", file=sys.stderr)
        return False

    if missing:
        fail(f"缺少 {len(missing)} 个输入文件", "推荐用 GUI 上传图片到 05_Video/uploads/，或检查 JSON 中的路径")
        for item in missing[:6]:
            print(f"    · {item}", file=sys.stderr)
        if len(missing) > 6:
            print(f"    · ... 还有 {len(missing) - 6} 个", file=sys.stderr)
        return False

    ok("片段配置与输入文件检查通过")
    return True


def main() -> int:
    print("Seedance 启动前体检\n")

    checks = [
        check_python(),
        check_ffmpeg(),
        check_api_key(),
        check_config(),
        check_segments(),
    ]

    print()
    if all(checks):
        print("全部检查通过，正在启动控制台…")
        print("浏览器地址：http://127.0.0.1:8765\n")
        return 0

    print("体检未通过，请先修复上述问题再启动。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
