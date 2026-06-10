import json, os, sys, base64, urllib.request, urllib.error
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"

api_key = open(r"E:\seedance_video_toolkit\API_Key\gemini_api_key.txt").read().strip()

# Try with a tiny 1-pixel image (smallest possible base64)
b64_1px = base64.b64encode(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82').decode()

body = {
    "contents": [{
        "role": "user",
        "parts": [
            {"inline_data": {"mime_type": "image/png", "data": b64_1px}},
            {"text": "What color is this image?"}
        ]
    }],
}

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={api_key}"
req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    print(f"SMALL IMAGE SUCCESS: {text}")
except urllib.error.HTTPError as e:
    err = e.read().decode()
    print(f"HTTP {e.code}: {err[:300]}")
except urllib.error.URLError as e:
    print(f"URL ERROR: {e.reason}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
