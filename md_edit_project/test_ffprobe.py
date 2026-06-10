import subprocess, sys, json
sys.path.insert(0, r"E:\seedance_video_toolkit\md_edit_project")

path = r"E:\seedance_video_toolkit\.md_cache\run_1780544433\clips\clip_001_绝境中的神兵利器.mp4"

print(f"File exists: {__import__('os').path.isfile(path)}")
print(f"Path repr: {repr(path)}")

# Test ffprobe
cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]
print(f"\nRunning: {cmd}")

# Without text=True to get raw bytes
r = subprocess.run(cmd, capture_output=True)
print(f"Return code: {r.returncode}")
print(f"stdout length: {len(r.stdout)}")
print(f"stderr: {r.stderr[:200]}")

# Try with text=True
r2 = subprocess.run(cmd, capture_output=True, text=True)
print(f"\nWith text=True:")
print(f"stdout length: {len(r2.stdout)}")
print(f"stdout empty? {not r2.stdout}")
