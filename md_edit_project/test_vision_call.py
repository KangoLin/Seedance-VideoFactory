import json, os, sys, base64, urllib.request, urllib.error
sys.path.insert(0, r"E:\seedance_video_toolkit\md_edit_project")
os.chdir(r"E:\seedance_video_toolkit\md_edit_project")
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"

api_key = open(r"E:\seedance_video_toolkit\API_Key\gemini_api_key.txt").read().strip()

# Use the newest run's first frame
frames_dir = r"E:\seedance_video_toolkit\.md_cache\run_1780543667\frames"
frame = os.path.join(frames_dir, "scene_0000.jpg")

with open(frame, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
print(f"Frame size: {len(b64)} bytes (base64)")

body = {
    "system_instruction": {"parts": [{"text": "You are a video analyst."}]},
    "contents": [{
        "role": "user",
        "parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
            {"text": "Describe this image briefly."}
        ]
    }],
    "generationConfig": {
        "temperature": 0.3,
    },
}

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={api_key}"
req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode())
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    print(f"SUCCESS: {text[:100]}")
except urllib.error.HTTPError as e:
    err = e.read().decode()
    print(f"HTTP {e.code}: {err[:200]}")
except urllib.error.URLError as e:
    print(f"URL ERROR: {e.reason}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
