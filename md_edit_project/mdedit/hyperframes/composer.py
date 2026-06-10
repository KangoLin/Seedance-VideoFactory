import json
import os
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template_meta(template: str) -> dict:
    path = TEMPLATES_DIR / template / "meta.json"
    if not path.exists():
        raise FileNotFoundError(f"Template meta not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_subtitles_html(subtitles: list[dict], style: dict) -> tuple[str, str]:
    font_size = style.get("font_size", 48)
    color = style.get("color", "#FFFFFF")
    stroke = style.get("stroke", "#000000")
    position_y = style.get("position_y", "80%")
    font_family = style.get("font_family", "'Microsoft YaHei', 'PingFang SC', sans-serif")

    elements = []
    animation_data = []
    for i, s in enumerate(subtitles):
        start = s["start"]
        end = s["end"]
        text = s["text"]
        elements.append(
            f'<div id="sub-{i}" class="clip" data-start="{start}" '
            f'data-duration="{end - start}" data-track-index="1" '
            f'style="position:absolute; left:0; width:100%; top:{position_y}; '
            f'font-size:{font_size}px; '
            f'font-family:{font_family}; color:{color}; '
            f'text-shadow:2px 2px 0 {stroke}, -2px -2px 0 {stroke}, '
            f'2px -2px 0 {stroke}, -2px 2px 0 {stroke}; '
            f'text-align:center; white-space:nowrap; '
            f'opacity:0;">{text}</div>'
        )
        animation_data.append({"i": i, "start": start, "end": end})

    anim_js = _build_animation_js(animation_data, style.get("animation_type", "fade"))
    return "\n".join(elements), anim_js


def _build_animation_js(anim_data: list[dict], anim_type: str) -> str:
    lines = []
    for a in anim_data:
        i, start, end = a["i"], a["start"], a["end"]
        exit_time = end - 0.3
        if anim_type == "bounce":
            lines.append(f'tl.fromTo("#sub-{i}", {{opacity:0,scale:0.3,y:60}}, {{opacity:1,scale:1,y:0,duration:0.5,ease:"back.out(1.7)"}}, {start});')
            lines.append(f'tl.to("#sub-{i}", {{opacity:0,scale:0.5,duration:0.3}}, {exit_time});')
        elif anim_type == "slide":
            direction = -60 if i % 2 == 0 else 60
            lines.append(f'tl.fromTo("#sub-{i}", {{opacity:0,x:{direction}}}, {{opacity:1,x:0,duration:0.4,ease:"power3.out"}}, {start});')
            lines.append(f'tl.to("#sub-{i}", {{opacity:0,x:-{direction},duration:0.3}}, {exit_time});')
        elif anim_type == "kinetic":
            scale_val = 1.8
            x_from = -120 if i % 2 == 0 else 120
            lines.append(f'tl.fromTo("#sub-{i}", {{opacity:0,x:{x_from},scale:{scale_val}}}, {{opacity:1,x:0,scale:1,duration:0.35,ease:"power4.out"}}, {start});')
            lines.append(f'tl.to("#sub-{i}", {{opacity:0,scale:0.5,duration:0.25}}, {exit_time});')
        else:  # fade
            lines.append(f'tl.fromTo("#sub-{i}", {{opacity:0,y:15}}, {{opacity:1,y:0,duration:0.4,ease:"power2.out"}}, {start});')
            lines.append(f'tl.to("#sub-{i}", {{opacity:0,duration:0.3}}, {exit_time});')
        lines.append(f'tl.set("#sub-{i}", {{opacity:0}}, {end});')
    lines.append('window.__timelines = window.__timelines || {};')
    lines.append('window.__timelines["clip-overlay"] = tl;')
    return "\n    ".join(lines)


def build_composition_html(
    template: str,
    subtitles: list[dict],
    width: int,
    height: int,
    duration: float,
    title: str = "",
) -> str:
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"build_composition_html: template={template}, subtitles={len(subtitles)}, width={width}, height={height}, duration={duration}")
    meta = _load_template_meta(template)
    style = meta.get("style", {})
    fontSize = style.get("font_size", 48)
    color = style.get("color", "#FFFFFF")
    stroke = style.get("stroke", "#000000")
    position_y = style.get("position_y", "80%")
    font_family = style.get("font_family", "'Microsoft YaHei', 'PingFang SC', sans-serif")
    anim_type = style.get("animation_type", "fade")

    has_title = bool(title) and style.get("show_title", True)
    extra_css = style.get("extra_css", "")

    subtitle_html, anim_js = build_subtitles_html(subtitles, style)

    title_html = ""
    if has_title:
        title_html = (
            f'<div id="clip-title" class="clip" data-start="0" '
            f'data-duration="2.5" data-track-index="0" '
            f'style="position:absolute; left:0; width:100%; top:40%; '
            f'font-size:{fontSize * 1.3}px; '
            f'font-family:{font_family}; color:{color}; font-weight:bold; '
            f'text-shadow:3px 3px 0 {stroke}, -3px -3px 0 {stroke}, '
            f'3px -3px 0 {stroke}, -3px 3px 0 {stroke}; '
            f'text-align:center; white-space:nowrap; opacity:0;">{title}</div>'
        )
        title_anim = (
            f'tl.fromTo("#clip-title", {{opacity:0,scale:0.5,y:-30}}, '
            f'{{opacity:1,scale:1,y:0,duration:0.6,ease:"back.out(1.7)"}}, 0);\n    '
            f'tl.to("#clip-title", {{opacity:0,scale:1.2,duration:0.4}}, 2.1);\n    '
        )
        anim_js = title_anim + anim_js
    # Ensure tl is created first, before any animation calls
    anim_js = "const tl = gsap.timeline({ paused: true });\n    " + anim_js

    html = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        "<style>\n*{margin:0;padding:0;box-sizing:border-box;}\n"
        "html,body{background:transparent;overflow:hidden;}\n"
        f'#root{{position:relative;width:{width}px;height:{height}px;overflow:hidden;}}\n'
        f'{extra_css}\n</style>\n'
        '<script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>\n'
        "</head>\n<body>\n"
        f'<div id="root" data-composition-id="clip-overlay" '
        f'data-start="0" data-width="{width}" data-height="{height}" '
        f'data-duration="{duration}">\n'
        f'{title_html}\n{subtitle_html}\n'
        "</div>\n<script>\n"
        f'{anim_js}\n'
        "</script>\n</body>\n</html>"
    )
    return html
