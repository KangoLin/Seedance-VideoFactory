import base64
import json
import os
import urllib.request
import urllib.error
import winreg


_GEMINI_BASE_URL = None


_PROXY_SET = False

def _setup_proxy():
    global _PROXY_SET
    if _PROXY_SET:
        return
    # Read from Windows registry
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        pe = winreg.QueryValueEx(key, "ProxyEnable")[0]
        ps = winreg.QueryValueEx(key, "ProxyServer")[0]
        if pe and ps and ps.strip():
            url = f"http://{ps.strip()}"
            os.environ["HTTP_PROXY"] = url
            os.environ["HTTPS_PROXY"] = url
            _PROXY_SET = True
    except Exception:
        pass


def _load_api_key(provider: str) -> str:
    fname = f"{provider}_api_key.txt"
    paths = [
        os.path.join("API_Key", fname),
        os.path.join(os.path.dirname(__file__), "..", "..", "API_Key", fname),
        os.path.join(os.path.dirname(__file__), "..", "API_Key", fname),
    ]
    for p in paths:
        if os.path.isfile(p):
            with open(p, "r") as f:
                return f.read().strip()
    raise RuntimeError(f"API key not found for {provider} (looked in API_Key/{fname})")


def _encode_frame(frame_path: str) -> dict:
    with open(frame_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {"inline_data": {"mime_type": "image/jpeg", "data": b64}}


def _get_video_duration(video_path: str) -> float:
    import subprocess
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    return float(r.stdout.strip())


def _encode_video(video_path: str) -> dict:
    """Base64 encode video, compressing if > 15MB."""
    MAX_SIZE = 15 * 1024 * 1024
    file_size = os.path.getsize(video_path)
    if file_size <= MAX_SIZE:
        with open(video_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return {"inline_data": {"mime_type": "video/mp4", "data": b64}}

    import subprocess as _sp, tempfile, uuid
    compressed = os.path.join(tempfile.gettempdir(), f"_vid_compress_{uuid.uuid4().hex[:8]}.mp4")
    try:
        dur = _get_video_duration(video_path)
        target_bitrate = int((MAX_SIZE * 8 * 0.85) / max(dur, 1))
        _sp.run([
            "ffmpeg", "-i", video_path,
            "-b:v", f"{target_bitrate}", "-maxrate", f"{int(target_bitrate*1.2)}",
            "-bufsize", f"{int(target_bitrate*2)}", "-y", compressed
        ], capture_output=True, timeout=300)
        with open(compressed, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return {"inline_data": {"mime_type": "video/mp4", "data": b64}}
    finally:
        try:
            os.unlink(compressed)
        except Exception:
            pass


def _call_gemini_text(
    system_prompt: str,
    user_prompt: str,
    video_path: str | None = None,
    model: str = "gemini-3.5-flash",
    temperature: float = 0.3,
) -> str:
    """Send video (optional) + text to Gemini, return free-form text response."""
    import time, subprocess, tempfile

    api_key = _load_api_key("gemini")
    base_url = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    parts = []
    if video_path:
        parts.append(_encode_video(video_path))
    parts.append({"text": user_prompt})

    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": temperature},
    }
    body_str = json.dumps(body, ensure_ascii=False)

    def _decode(b):
        return b.decode("utf-8", errors="replace") if b else ""

    last_err = None
    for attempt in range(3):
        body_path = os.path.join(tempfile.gettempdir(), f"_gemini_text_{attempt}.json")
        try:
            with open(body_path, "w", encoding="utf-8") as f:
                f.write(body_str)
            url = f"{base_url}/models/{model}:generateContent?key={api_key}"
            curl_args = [
                "curl.exe", "-s", "-X", "POST", url,
                "-H", "Content-Type: application/json",
                "-d", f"@{body_path}",
                "-x", "http://127.0.0.1:7890",
                "--max-time", "300",
                "--retry", "2", "--retry-delay", "5", "--retry-all-errors",
            ]
            r = subprocess.run(curl_args, capture_output=True, timeout=320)
            if r.returncode != 0:
                raise RuntimeError(f"curl exit {r.returncode}: {_decode(r.stderr)[:300]}")
            result = json.loads(_decode(r.stdout))
            break
        except json.JSONDecodeError as e:
            last_err = f"JSON parse error: {e}. Response: {_decode(r.stdout)[:200]}"
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(2 ** attempt)
        finally:
            try:
                os.unlink(body_path)
            except Exception:
                pass
    else:
        raise RuntimeError(f"Gemini API failed after 3 retries: {last_err}")

    candidates = result.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"No candidates: {json.dumps(result, ensure_ascii=False)[:500]}")
    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    if not text or not text.strip():
        raise RuntimeError(f"Empty response: {json.dumps(result, ensure_ascii=False)[:500]}")
    return text.strip()


def _call_gemini(
    system_prompt: str,
    user_prompt: str,
    response_schema: dict,
    frame_paths: list[str] | None = None,
    model: str = "gemini-3.5-flash",
    temperature: float = 0.2,
) -> dict:
    api_key = _load_api_key("gemini")
    base_url = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    contents = []
    if frame_paths:
        parts = [_encode_frame(p) for p in frame_paths]
        parts.append({"text": user_prompt})
        contents.append({"role": "user", "parts": parts})
    else:
        contents.append({"role": "user", "parts": [{"text": user_prompt}]})

    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
        },
    }
    body_str = json.dumps(body, ensure_ascii=False)

    import time, subprocess, tempfile, os as _os

    def _decode(b):
        return b.decode("utf-8", errors="replace") if b else ""

    last_err = None
    for attempt in range(3):
        body_path = _os.path.join(tempfile.gettempdir(), f"_gemini_body_{attempt}.json")
        try:
            with open(body_path, "w", encoding="utf-8") as f:
                f.write(body_str)
            url = f"{base_url}/models/{model}:generateContent?key={api_key}"
            curl_args = [
                "curl.exe", "-s", "-X", "POST", url,
                "-H", "Content-Type: application/json",
                "-d", f"@{body_path}",
                "-x", "http://127.0.0.1:7890",
                "--max-time", "180",
                "--retry", "2", "--retry-delay", "5", "--retry-all-errors",
            ]
            r = subprocess.run(curl_args, capture_output=True, timeout=200)
            if r.returncode != 0:
                raise RuntimeError(f"curl exit {r.returncode}: {_decode(r.stderr)[:300]}")
            result = json.loads(_decode(r.stdout))
            break
        except json.JSONDecodeError as e:
            last_err = f"JSON parse error: {e}. Response: {_decode(r.stdout)[:200]}"
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(2 ** attempt)
        finally:
            try:
                _os.unlink(body_path)
            except Exception:
                pass
    else:
        raise RuntimeError(f"Gemini API failed after 3 retries: {last_err}")

    candidates = result.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"No candidates in Gemini response: {json.dumps(result, ensure_ascii=False)[:500]}")
    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text")
    if not text or not text.strip():
        raise RuntimeError(f"Gemini returned empty text: {json.dumps(result, ensure_ascii=False)[:500]}")
    return json.loads(text), text


def _call_deepseek(
    system_prompt: str,
    user_prompt: str,
    response_schema: dict,
    frame_paths: list[str] | None = None,
    model: str = "deepseek-chat",
    temperature: float = 0.2,
) -> dict:
    _setup_proxy()
    api_key = _load_api_key("deepseek")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    url = f"{base_url}/v1/chat/completions"

    if frame_paths:
        raise RuntimeError("DeepSeek does not support image input; use Gemini for vision tasks")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    user_content = user_prompt
    if response_schema:
        schema_hint = "\n\nOutput ONLY valid JSON matching this schema:\n" + json.dumps(response_schema, indent=2, ensure_ascii=False)
        user_content += schema_hint
    messages.append({"role": "user", "content": user_content})

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"DeepSeek API error {e.code}: {e.read().decode()}")

    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError(f"No choices in DeepSeek response: {result}")
    content = choices[0].get("message", {}).get("content")
    if not content or not content.strip():
        raise RuntimeError(f"DeepSeek returned empty or null content: {result}")
    return json.loads(content)


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    response_schema: dict,
    frame_paths: list[str] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    provider: str = "gemini",
    _raw_output: list | None = None,
) -> dict:
    _setup_proxy()

    if provider == "gemini":
        model = model or "gemini-3.5-flash"
        result, raw_text = _call_gemini(system_prompt, user_prompt, response_schema, frame_paths, model, temperature)
        if _raw_output is not None:
            _raw_output.append(raw_text)
        return result
    elif provider == "deepseek":
        if frame_paths:
            raise RuntimeError("DeepSeek does not support image input")
        model = model or "deepseek-chat"
        return _call_deepseek(system_prompt, user_prompt, response_schema, frame_paths, model, temperature)
    else:
        raise RuntimeError(f"Unknown provider: {provider}")
