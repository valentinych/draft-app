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
    """Load mapping from FPL ids to Mantra ids.

    Both S3 and local files may contain partial data.  Previously the function
    returned immediately when an S3 file was present which meant any additional
    entries stored locally were ignored.  As a result only a subset of players
    had their metadata fetched (55 instead of the expected 144).  We now merge
    the two sources and emit debug information so operators can verify the
    counts.
    """

    s3_map: Dict[str, int] = {}
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key()
        if bucket and key:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict):
                try:
                    s3_map = {str(k): int(v) for k, v in data.items()}
                except Exception:
                    s3_map = {}

    local_map: Dict[str, int] = {}
    if MAP_FILE.exists():
        try:
            with MAP_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                local_map = {str(k): int(v) for k, v in data.items()}
        except Exception:
            local_map = {}

    merged = {**s3_map, **local_map}
    print(
        f"[MAP] load_player_map s3={len(s3_map)} local={len(local_map)} merged={len(merged)}"
    )
    return merged

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
