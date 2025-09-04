import json
import os
import tempfile
from pathlib import Path
from typing import Dict

from .epl_services import _s3_enabled, _s3_bucket, _s3_get_json, _s3_put_json

BASE_DIR = Path(__file__).resolve().parent.parent
MAP_FILE = BASE_DIR / "data" / "player_map.json"
MAP_FILE.parent.mkdir(parents=True, exist_ok=True)

def _s3_key() -> str:
    return os.getenv("DRAFT_S3_PLAYER_MAP_KEY", "player_map.json")

def load_player_map() -> Dict[str, int]:
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key()
        if bucket and key:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict):
                try:
                    return {str(k): int(v) for k, v in data.items()}
                except Exception:
                    return {}
    if MAP_FILE.exists():
        try:
            with MAP_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
        except Exception:
            pass
    return {}

def save_player_map(mapping: Dict[str, int]) -> None:
    payload = {str(k): int(v) for k, v in mapping.items()}
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key()
        if bucket and key and not _s3_put_json(bucket, key, payload):
            print(f"[MAP] Failed to save to s3://{bucket}/{key}")
    fd, tmp = tempfile.mkstemp(prefix="player_map_", suffix=".json", dir=str(MAP_FILE.parent))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MAP_FILE)
