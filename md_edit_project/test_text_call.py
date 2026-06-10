import json, os, sys, urllib.request, urllib.error
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"

api_key = open(r"E:\seedance_video_toolkit\API_Key\gemini_api_key.txt").read().strip()

# Text-only request
body = {
    "contents": [{"role": "user", "parts": [{"text": "Say hello"}]}],
}

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={api_key}"
req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    print(f"TEXT-ONLY SUCCESS: {text}")
except urllib.error.HTTPError as e:
    err = e.read().decode()
    print(f"HTTP {e.code}: {err[:200]}")
except urllib.error.URLError as e:
    print(f"URL ERROR: {e.reason}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
