import json, os, sys, base64, http.client, ssl
sys.path.insert(0, r"E:\seedance_video_toolkit\md_edit_project")
os.chdir(r"E:\seedance_video_toolkit\md_edit_project")

api_key = open(r"E:\seedance_video_toolkit\API_Key\gemini_api_key.txt").read().strip()

frames_dir = r"E:\seedance_video_toolkit\.md_cache\run_1780543667\frames"
frame = os.path.join(frames_dir, "scene_0000.jpg")

with open(frame, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

body = {
    "contents": [{
        "role": "user",
        "parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
            {"text": "Describe this image briefly"}
        ]
    }],
}
body_bytes = json.dumps(body).encode()

conn = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=60)
conn.set_tunnel("127.0.0.1", 7890)  # proxy tunnel
conn.connect()
url = f"/v1beta/models/gemini-3.5-flash:generateContent?key={api_key}"
conn.request("POST", url, body=body_bytes, headers={"Content-Type": "application/json"})
resp = conn.getresponse()
data = resp.read().decode()
print(f"Status: {resp.status}")
if resp.status == 200:
    result = json.loads(data)
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    print(f"SUCCESS: {text[:100]}")
else:
    print(f"Error: {data[:300]}")
conn.close()
