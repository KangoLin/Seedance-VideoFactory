import asyncio
import json
import os
import time
import urllib.parse
from pathlib import Path
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from mdedit.cache import Cache
from mdedit.pipeline import run_pipeline, analyze_clips_gemini, analyze_single_clip, render_smart_cut, render_fine_cut
from mdedit.cinematic import render_cinematic as _render_cinematic
from mdedit.ffmpeg import get_video_info
from mdedit.llm import _setup_proxy

_setup_proxy()

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output" / "renders"

app = FastAPI(title="md-edit Web UI")


def _list_clips() -> list[dict]:
    import re
    ep_titles = {}
    ep_path = ROOT / "05_Video" / "workspace" / "episodes.json"
    if ep_path.exists():
        try:
            data = json.loads(ep_path.read_text(encoding="utf-8"))
            for ep in data.get("episodes", []):
                no = ep.get("episode_no")
                title = ep.get("title", "")
                if no and title:
                    ep_titles[no] = title
        except Exception:
            pass

    # Collect all files, dedup by episode number
    # Prefer exact "ep-{n}-concat" slug; tie-break by highest version
    best: dict[int, dict] = {}
    for src_dir in [ROOT / "output" / "concat", ROOT / "output" / "exports"]:
        if not src_dir.is_dir():
            continue
        for f in sorted(src_dir.glob("*concat*.mp4")):
            stem = f.stem
            vm = re.search(r"_v(\d+)$", stem)
            if not vm:
                continue
            version = int(vm.group(1))
            # extract episode number
            em = re.search(r"ep-(\d+)", f.name)
            if not em:
                continue
            episode_no = int(em.group(1))
            # normalize base slug (strip platform suffix)
            base = re.sub(r"_(TikTok|YouTube)$", "", vm.string[:vm.start()])
            # exact concat (no -clip suffix) is preferred
            is_exact = bool(re.match(rf"ep-{episode_no}-concat$", base))
            rel = str(f.relative_to(ROOT)).replace("\\", "/")
            prev = best.get(episode_no)
            if prev is None:
                should_replace = True
            elif is_exact and not prev.get("is_exact"):
                should_replace = True
            elif is_exact == prev.get("is_exact") and version > prev["version"]:
                should_replace = True
            else:
                should_replace = False
            if should_replace:
                t = ep_titles.get(episode_no)
                display = f"第{episode_no}集 {t}" if t else f.name
                best[episode_no] = {
                    "path": rel,
                    "name": f.name,
                    "display": display,
                    "dir": str(src_dir.relative_to(ROOT)),
                    "size": f.stat().st_size,
                    "version": version,
                    "is_exact": is_exact,
                }
    clips = []
    for k, v in sorted(best.items()):
        v.pop("is_exact", None)
        clips.append(v)
    return clips


def _list_bgm() -> list[dict]:
    files = []
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        files.extend(sorted(ROOT.glob(ext)))
    return [{
        "path": str(f.relative_to(ROOT)),
        "name": f.name,
    } for f in files]


@app.get("/api/clips")
def api_list_clips(q: str = Query("", description="Search filter")):
    clips = _list_clips()
    if q:
        ql = q.lower()
        clips = [c for c in clips if ql in c["name"].lower() or ql in c.get("display", c["name"]).lower()]
    return {"clips": clips}


@app.get("/api/languages")
def api_list_languages():
    from mdedit.languages import LANGUAGES, REGIONS
    return {"languages": LANGUAGES, "regions": REGIONS}

@app.get("/api/bgm")
def api_list_bgm():
    return {"bgm": _list_bgm()}


@app.get("/api/file/{path:path}")
def api_get_file(path: str):
    full = ROOT / path
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(full))


@app.post("/api/run")
async def api_run(body: dict):
    input_patterns = body.get("input", [])
    provider = body.get("provider", "gemini")
    min_score = body.get("min_score", 60)
    max_clips = body.get("max_clips", 10)
    enhance = body.get("enhance", False)
    enhance_template = body.get("enhance_template", "douyin")
    supervisor = body.get("supervisor", True)
    target_language = body.get("target_language", "en")

    if not input_patterns:
        raise HTTPException(400, "No input clips selected")

    work_dir = os.path.join(ROOT, "output", "pipeline", f"run_{int(time.time())}")

    async def event_stream():
        def send(msg: str):
            return f"data: {json.dumps({'msg': msg}, ensure_ascii=False)}\n\n"

        def send_json(obj: dict):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        yield send("Starting AutoClip pipeline...")

        full_path = os.path.join(ROOT, input_patterns[0])
        if not os.path.isfile(full_path):
            yield send(f"ERROR: File not found: {full_path}")
            return

        info = get_video_info(full_path)
        yield send(f"Video: {os.path.basename(full_path)} | {info['duration']:.0f}s | {info['width']}x{info['height']}")

        progress_queue = asyncio.Queue()

        async def progress_feeder():
            def on_progress(msg: str):
                progress_queue.put_nowait(msg)

            loop = asyncio.get_event_loop()
            try:
                manifest = await loop.run_in_executor(
                    None,
                    lambda: run_pipeline(
                        video_path=full_path,
                        work_dir=work_dir,
                        cache=None,
                        force=True,
                        provider=provider,
                        min_score=min_score,
                        max_clips=max_clips,
                        progress_callback=on_progress,
                        enhance=enhance,
                        enhance_template=enhance_template,
                        supervisor=supervisor,
                        target_language=target_language,
                    ),
                )
                progress_queue.put_nowait("__DONE__")
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[PIPELINE ERROR] {e}\n{tb}")
                progress_queue.put_nowait(f"__ERROR__:{e}")

        asyncio.create_task(progress_feeder())

        manifest = None
        while True:
            msg = await progress_queue.get()
            if msg == "__DONE__":
                break
            if msg.startswith("__ERROR__:"):
                yield send(f"ERROR: {msg[len('__ERROR__:'):]}")
                return
            yield send(msg)

        manifest_path = os.path.join(work_dir, "manifest.json")
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        yield send_json({
            "clips_ready": True,
            "work_dir": work_dir,
            "provider": provider,
            "target_language": target_language,
            "clips": manifest["clips"],
            "video_path": manifest["video_path"],
            "duration": manifest["duration"],
            "supervisor_suggestions": manifest.get("supervisor_suggestions"),
            "raw_analysis": manifest.get("raw_analysis", ""),
        })

        yield send("DONE")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/analyze_clips")
async def api_analyze_clips(body: dict):
    work_dir = body.get("work_dir", "")
    if not work_dir:
        raise HTTPException(400, "work_dir is required")

    full_work_dir = os.path.join(ROOT, work_dir) if not os.path.isabs(work_dir) else work_dir

    async def event_stream():
        def send(msg: str):
            return f"data: {json.dumps({'msg': msg}, ensure_ascii=False)}\n\n"

        def send_json(obj: dict):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        yield send("Starting clip analysis...")

        progress_queue = asyncio.Queue()

        async def progress_feeder():
            def on_progress(msg: str):
                progress_queue.put_nowait(msg)

            loop = asyncio.get_event_loop()
            try:
                manifest = await loop.run_in_executor(
                    None,
                    lambda: analyze_clips_gemini(
                        work_dir=full_work_dir,
                        progress_callback=on_progress,
                    ),
                )
                progress_queue.put_nowait("__DONE__")
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[ANALYZE CLIPS ERROR] {e}\n{tb}")
                progress_queue.put_nowait(f"__ERROR__:{e}")

        asyncio.create_task(progress_feeder())

        while True:
            msg = await progress_queue.get()
            if msg == "__DONE__":
                break
            if msg.startswith("__ERROR__:"):
                yield send(f"ERROR: {msg[len('__ERROR__:'):]}")
                return
            yield send(msg)

        yield send_json({
            "clips_analyzed": True,
            "clips": manifest["clips"],
        })
        yield send("DONE")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/analyze_clip/{clip_id}")
async def api_analyze_single_clip(clip_id: int, body: dict):
    work_dir = body.get("work_dir", "")
    if not work_dir:
        raise HTTPException(400, "work_dir is required")

    full_work_dir = os.path.join(ROOT, work_dir) if not os.path.isabs(work_dir) else work_dir

    async def event_stream():
        def send(msg: str):
            return f"data: {json.dumps({'msg': msg}, ensure_ascii=False)}\n\n"

        def send_json(obj: dict):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        yield send(f"Starting Gemini analysis for clip #{clip_id}...")

        progress_queue = asyncio.Queue()

        async def progress_feeder():
            def on_progress(msg: str):
                progress_queue.put_nowait(msg)

            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: analyze_single_clip(
                        work_dir=full_work_dir,
                        clip_id=clip_id,
                        progress_callback=on_progress,
                    ),
                )
                progress_queue.put_nowait("__DONE__")
                progress_queue.put_nowait(result)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[ANALYZE CLIP {clip_id} ERROR] {e}\n{tb}")
                progress_queue.put_nowait(f"__ERROR__:{e}")

        asyncio.create_task(progress_feeder())

        final_result = None
        while True:
            msg = await progress_queue.get()
            if msg == "__DONE__":
                final_result = await progress_queue.get()
                break
            if isinstance(msg, str) and msg.startswith("__ERROR__:"):
                yield send(f"ERROR: {msg[len('__ERROR__:'):]}")
                return
            yield send(msg)

        yield send_json({
            "clip_analyzed": True,
            "clip": final_result.get("clip") if final_result else None,
        })
        yield send("DONE")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/smart_render")
async def api_smart_render(body: dict):
    work_dir = body.get("work_dir", "")
    selected_clip_ids = body.get("selected_clip_ids")
    bgm = body.get("bgm")

    if not work_dir:
        raise HTTPException(400, "work_dir is required")

    full_work_dir = os.path.join(ROOT, work_dir) if not os.path.isabs(work_dir) else work_dir
    bgm_path = None
    if bgm:
        bgm_full = os.path.join(ROOT, bgm) if not os.path.isabs(bgm) else bgm
        if os.path.isfile(bgm_full):
            bgm_path = bgm_full

    output_name = f"smart_cut_{int(time.time())}.mp4"
    output_dir = ROOT / "output" / "renders"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / output_name)

    async def event_stream():
        def send(msg: str):
            return f"data: {json.dumps({'msg': msg}, ensure_ascii=False)}\n\n"

        def send_json(obj: dict):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        yield send("Starting smart cut render...")

        progress_queue = asyncio.Queue()

        async def progress_feeder():
            def on_progress(msg: str):
                progress_queue.put_nowait(msg)

            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: render_smart_cut(
                        work_dir=full_work_dir,
                        selected_clip_ids=selected_clip_ids,
                        bgm_path=bgm_path,
                        output_path=output_path,
                        progress_callback=on_progress,
                    ),
                )
                progress_queue.put_nowait("__DONE__")
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[SMART RENDER ERROR] {e}\n{tb}")
                progress_queue.put_nowait(f"__ERROR__:{e}")

        asyncio.create_task(progress_feeder())

        while True:
            msg = await progress_queue.get()
            if msg == "__DONE__":
                break
            if msg.startswith("__ERROR__:"):
                yield send(f"ERROR: {msg[len('__ERROR__:'):]}")
                return
            yield send(msg)

        rel_path = str(Path(output_path).relative_to(ROOT)).replace("\\", "/")
        yield send_json({
            "render_done": True,
            "output_path": rel_path,
            "output_name": output_name,
        })
        yield send("DONE")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/render")
async def api_render(body: dict):
    work_dir = body.get("work_dir", "")
    accepted_suggestions = body.get("accepted_suggestions", {})
    selected_clip_ids = body.get("selected_clip_ids")
    bgm = body.get("bgm")
    target_language = body.get("target_language", "en")
    provider = body.get("provider", "gemini")

    if not work_dir:
        raise HTTPException(400, "work_dir is required")

    full_work_dir = os.path.join(ROOT, work_dir) if not os.path.isabs(work_dir) else work_dir
    bgm_path = None
    if bgm:
        bgm_full = os.path.join(ROOT, bgm) if not os.path.isabs(bgm) else bgm
        if os.path.isfile(bgm_full):
            bgm_path = bgm_full

    output_name = f"fine_cut_{int(time.time())}.mp4"
    output_dir = ROOT / "output" / "renders"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / output_name)

    async def event_stream():
        def send(msg: str):
            return f"data: {json.dumps({'msg': msg}, ensure_ascii=False)}\n\n"

        def send_json(obj: dict):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        yield send("Starting fine-cut render...")

        progress_queue = asyncio.Queue()

        async def progress_feeder():
            def on_progress(msg: str):
                progress_queue.put_nowait(msg)

            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: render_fine_cut(
                        work_dir=full_work_dir,
                        accepted_suggestions=accepted_suggestions,
                        selected_clip_ids=selected_clip_ids,
                        bgm_path=bgm_path,
                        output_path=output_path,
                        progress_callback=on_progress,
                        target_language=target_language,
                        provider=provider,
                    ),
                )
                progress_queue.put_nowait("__DONE__")
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[RENDER ERROR] {e}\n{tb}")
                progress_queue.put_nowait(f"__ERROR__:{e}")

        asyncio.create_task(progress_feeder())

        while True:
            msg = await progress_queue.get()
            if msg == "__DONE__":
                break
            if msg.startswith("__ERROR__:"):
                yield send(f"ERROR: {msg[len('__ERROR__:'):]}")
                return
            yield send(msg)

        rel_path = str(Path(output_path).relative_to(ROOT)).replace("\\", "/")
        yield send_json({
            "render_done": True,
            "output_path": rel_path,
            "output_name": output_name,
        })
        yield send("DONE")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/cinematic_render")
async def api_cinematic_render(body: dict):
    video_path = body.get("video_path", "")
    bgm = body.get("bgm")
    provider = body.get("provider", "gemini")
    edit_intensity = body.get("edit_intensity", 0)
    target_language = body.get("target_language", "en")

    if not video_path:
        raise HTTPException(400, "video_path is required")

    full_video_path = os.path.join(ROOT, video_path) if not os.path.isabs(video_path) else video_path
    if not os.path.isfile(full_video_path):
        raise HTTPException(404, f"Video not found: {full_video_path}")

    bgm_path = None
    if bgm:
        bgm_full = os.path.join(ROOT, bgm) if not os.path.isabs(bgm) else bgm
        if os.path.isfile(bgm_full):
            bgm_path = bgm_full

    output_name = f"cinematic_{int(time.time())}.mp4"
    output_dir = ROOT / "output" / "renders"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / output_name)
    work_dir = os.path.join(ROOT, "output", "pipeline", f"cinematic_{int(time.time())}")

    async def event_stream():
        def send(msg: str):
            return f"data: {json.dumps({'msg': msg}, ensure_ascii=False)}\n\n"

        def send_json(obj: dict):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        yield send("Starting cinematic render...")

        progress_queue = asyncio.Queue()

        async def progress_feeder():
            def on_progress(msg: str):
                progress_queue.put_nowait(msg)

            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: _render_cinematic(
                        video_path=full_video_path,
                        work_dir=work_dir,
                        output_path=output_path,
                        bgm_path=bgm_path,
                        provider=provider,
                        edit_intensity=edit_intensity,
                        target_language=target_language,
                        progress_callback=on_progress,
                    ),
                )
                progress_queue.put_nowait("__DONE__")
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[CINEMATIC RENDER ERROR] {e}\n{tb}")
                progress_queue.put_nowait(f"__ERROR__:{e}")

        asyncio.create_task(progress_feeder())

        while True:
            msg = await progress_queue.get()
            if msg == "__DONE__":
                break
            if msg.startswith("__ERROR__:"):
                yield send(f"ERROR: {msg[len('__ERROR__:'):]}")
                return
            yield send(msg)

        rel_path = str(Path(output_path).relative_to(ROOT)).replace("\\", "/")
        yield send_json({
            "cinematic_done": True,
            "output_path": rel_path,
            "output_name": output_name,
        })
        yield send("DONE")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/thumb")
def api_thumbnail(path: str = Query("")):
    if not path:
        raise HTTPException(400, "path is required")
    full = ROOT / path
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "File not found")
    thumb_dir = ROOT / "output" / "renders" / ".thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{full.stem}.jpg"
    if not thumb_path.exists():
        import subprocess
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.5", "-i", str(full),
             "-vframes", "1", "-q:v", "5", "-f", "image2pipe", "-"],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0 or len(r.stdout) < 100:
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(full),
                 "-vframes", "1", "-q:v", "5", "-f", "image2pipe", "-"],
                capture_output=True, timeout=30,
            )
            if r2.returncode == 0 and len(r2.stdout) > 100:
                thumb_path.write_bytes(r2.stdout)
            else:
                raise HTTPException(500, "Thumbnail generation failed")
        else:
            thumb_path.write_bytes(r.stdout)
    return FileResponse(str(thumb_path), media_type="image/jpeg")


@app.delete("/api/file/{path:path}")
def api_delete_file(path: str):
    full = ROOT / path
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "File not found")
    full.unlink()
    thumb_dir = ROOT / "output" / "renders" / ".thumbs"
    thumb_path = thumb_dir / f"{full.stem}.jpg"
    if thumb_path.exists():
        thumb_path.unlink()
    return {"deleted": path}


@app.get("/api/outputs")
def api_list_outputs():
    outputs = []
    out_dir = ROOT / "output" / "renders"
    if out_dir.is_dir():
        for f in sorted(out_dir.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
            rel = str(f.relative_to(ROOT)).replace("\\", "/")
            outputs.append({
                "name": f.name,
                "path": rel,
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
                "thumb": f"/api/thumb?path={urllib.parse.quote(rel)}",
            })
    return {"outputs": outputs}


@app.get("/")
async def index():
    html = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    ver = _read_toolkit_version()
    html = html.replace("{{MDEDIT_VERSION}}", ver)
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Expires": "0",
        },
    )


def _read_toolkit_version() -> str:
    try:
        vf = ROOT / "VERSION"
        return vf.read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766)
