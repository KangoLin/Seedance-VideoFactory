import subprocess, os, sys

path = r"E:\seedance_video_toolkit\.md_cache\run_1780544785\clips\clip_001_硬核武器登场.mp4"

# Test with list (no shell)
print("=== Test 1: list args, no shell ===")
r1 = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path], capture_output=True)
print(f"rc={r1.returncode}, stdout={len(r1.stdout)} bytes, stderr={r1.stderr[:200]}")

# Test with shell=True
print("\n=== Test 2: shell=True, quoted path ===")
r2 = subprocess.run(f'ffprobe -v quiet -print_format json -show_format -show_streams "{path}"', capture_output=True, shell=True)
print(f"rc={r2.returncode}, stdout={len(r2.stdout)} bytes, stderr={r2.stderr[:200]}")

# Test with explicit encoding
print("\n=== Test 3: list args, text=True, encoding=utf-8 ===")
r3 = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path], capture_output=True, text=True, encoding="utf-8")
print(f"rc={r3.returncode}, stdout={len(r3.stdout)} chars, stderr={r3.stderr[:200]}")
