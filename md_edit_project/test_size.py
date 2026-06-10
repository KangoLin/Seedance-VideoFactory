import json, os, sys, base64, urllib.request, urllib.error
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"

api_key = open(r"E:\seedance_video_toolkit\API_Key\gemini_api_key.txt").read().strip()

# Test with a real frame but different quality levels
frames_dir = r"E:\seedance_video_toolkit\.md_cache\run_1780543667\frames"
frame = os.path.join(frames_dir, "scene_0000.jpg")

with open(frame, "rb") as f:
    data = f.read()
    b64_full = base64.b64encode(data).decode()

# Try with just 10KB of the image (first bytes)
b64_truncated = base64.b64encode(data[:10000]).decode()

for name, b64, expected_size in [
    ("Full image", b64_full, len(data)),
    ("10KB truncated", b64_truncated, 10000),
]:
    print(f"\nTesting {name} ({expected_size} bytes)...")
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                {"text": "Describe this image briefly"}
            ]
        }],
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={api_key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        print(f"  SUCCESS: {text[:80]}")
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"  HTTP {e.code}: {err[:150]}")
    except urllib.error.URLError as e:
        print(f"  URL ERROR: {e.reason}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
