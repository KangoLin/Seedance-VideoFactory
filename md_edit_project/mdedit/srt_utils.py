import re
import json


def time_to_seconds(t: str) -> float:
    if "," in t:
        t = t.replace(",", ".")
    parts = t.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def seconds_to_srt(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def parse_srt(text: str) -> list[dict]:
    entries = []
    blocks = re.split(r"\n\n+", text.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0])
        except ValueError:
            continue
        time_match = re.match(r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})", lines[1])
        if not time_match:
            continue
        start = time_match.group(1)
        end = time_match.group(2)
        text = "\n".join(lines[2:])
        entries.append({
            "index": index,
            "start": start,
            "end": end,
            "start_sec": time_to_seconds(start),
            "end_sec": time_to_seconds(end),
            "text": text,
        })
    return entries


def chunk_srt(entries: list[dict], interval_minutes: int = 30) -> list[dict]:
    if not entries:
        return []
    interval_sec = interval_minutes * 60
    start_time = entries[0]["start_sec"]
    chunks = []
    current_chunk = []
    for e in entries:
        if e["end_sec"] - start_time > interval_sec and current_chunk:
            chunks.append({
                "chunk_index": len(chunks),
                "text": "\n".join(x["text"] for x in current_chunk),
                "srt_entries": current_chunk,
            })
            current_chunk = []
            start_time = e["start_sec"]
        current_chunk.append(e)
    if current_chunk:
        chunks.append({
            "chunk_index": len(chunks),
            "text": "\n".join(x["text"] for x in current_chunk),
            "srt_entries": current_chunk,
        })
    return chunks


def srt_to_text(entries: list[dict]) -> str:
    return "\n".join(e["text"] for e in entries)
