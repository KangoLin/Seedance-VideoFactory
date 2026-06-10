import os
import logging

logger = logging.getLogger(__name__)

# Use HF mirror for users in China
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

try:
    from faster_whisper import WhisperModel

    _model = None

    def _get_model(model_size: str = "base"):
        global _model
        if _model is None:
            logger.info(f"Loading faster-whisper model '{model_size}' (CPU, int8)...")
            _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        return _model

    def transcribe(video_path: str, srt_path: str, model_size: str = "base", language: str = None) -> str:
        model = _get_model(model_size)
        logger.info(f"Transcribing {video_path}...")
        segments, info = model.transcribe(video_path, language=language, beam_size=5)
        lines = []
        for i, seg in enumerate(segments, 1):
            start = seg.start
            end = seg.end
            text = seg.text.strip()
            if not text:
                continue
            start_srt = _fmt_time(start)
            end_srt = _fmt_time(end)
            lines.append(f"{i}")
            lines.append(f"{start_srt} --> {end_srt}")
            lines.append(text)
            lines.append("")
        srt_content = "\n".join(lines)
        os.makedirs(os.path.dirname(srt_path), exist_ok=True)
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        logger.info(f"SRT saved to {srt_path} ({len(lines)//3} segments)")
        return srt_path

    def _fmt_time(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

except ImportError:
    logger.warning("faster-whisper not installed; transcription disabled")

    def transcribe(video_path: str, srt_path: str, **kwargs) -> str:
        raise RuntimeError("faster-whisper is not installed. Run: pip install faster-whisper")
