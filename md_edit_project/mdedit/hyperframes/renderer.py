import os
import subprocess
import shutil
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "template_project"


def _copy_template(project_dir: str):
    os.makedirs(project_dir, exist_ok=True)
    for item in TEMPLATE_DIR.iterdir():
        dst = os.path.join(project_dir, item.name)
        if item.is_dir():
            shutil.copytree(str(item), dst, dirs_exist_ok=True)
        else:
            shutil.copy2(str(item), dst)


def render_overlay(
    html: str,
    project_dir: str,
    output: str = "overlay.mov",
    quality: str = "draft",
    workers: int = 1,
) -> str:
    _copy_template(project_dir)
    index_path = os.path.join(project_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    cmd = [
        'npx.cmd', 'hyperframes', 'render',
        '--format', 'mov',
        '--output', output,
        '--quality', quality,
        '--workers', str(workers),
    ]
    r = subprocess.run(cmd, cwd=project_dir, capture_output=True, timeout=900, shell=False)
    output_path = os.path.join(project_dir, output)
    if not os.path.isfile(output_path):
        err = r.stderr.decode("utf-8", errors="replace")[:1000]
        out = r.stdout.decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(
            f"Render failed (exit {r.returncode}). stderr: {err}"
        )
    return output_path
