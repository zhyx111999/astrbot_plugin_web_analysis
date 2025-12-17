from __future__ import annotations
import json
import time
import hashlib
from pathlib import Path

class DiskCache:
    def __init__(self, cache_dir: Path, ttl_sec: int = 3600):
        self.cache_dir = cache_dir
        self.ttl_sec = int(ttl_sec)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _fname(self, key: str) -> Path:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{h}.json"

    def get(self, key: str):
        fp = self._fname(key)
        if not fp.exists():
            return None
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
            if time.time() - float(obj.get("ts", 0)) > self.ttl_sec:
                fp.unlink(missing_ok=True)
                return None
            return obj.get("value")
        except:
            return None

    def set(self, key: str, value):
        fp = self._fname(key)
        obj = {"ts": time.time(), "value": value}
        fp.write_text(json.dumps(obj), encoding="utf-8")

    def clear(self) -> int:
        n = 0
        for f in self.cache_dir.glob("*.json"):
            try:
                f.unlink()
                n += 1
            except: pass
        return n
