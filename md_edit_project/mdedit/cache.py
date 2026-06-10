import hashlib
import json
import os
import time


class Cache:
    def __init__(self, cache_dir: str, ttl: int = 3600):
        self.cache_dir = cache_dir
        self.ttl = ttl
        os.makedirs(cache_dir, exist_ok=True)

    def _key_path(self, key: str) -> str:
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"{h}.json")

    def get(self, key: str) -> dict | None:
        path = self._key_path(key)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        elapsed = time.time() - data.get("_cached_at", 0)
        if elapsed > self.ttl:
            os.remove(path)
            return None
        # invalidate if source mtimes changed
        src_mtimes = data.get("_source_mtimes", {})
        for src_path, mtime in src_mtimes.items():
            if not os.path.isfile(src_path) or os.path.getmtime(src_path) != mtime:
                os.remove(path)
                return None
        return data.get("result")

    def set(self, key: str, result, source_paths: list[str] | None = None) -> dict:
        source_mtimes = {}
        if source_paths:
            for p in source_paths:
                if os.path.isfile(p):
                    source_mtimes[p] = os.path.getmtime(p)
        data = {
            "_cached_at": time.time(),
            "_source_mtimes": source_mtimes,
            "result": result,
        }
        path = self._key_path(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return result

    def clear_all(self):
        for name in os.listdir(self.cache_dir):
            if name.endswith(".json"):
                os.remove(os.path.join(self.cache_dir, name))
