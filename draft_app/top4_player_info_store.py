import json
import os
import tempfile
from pathlib import Path
from typing import Dict

from .top4_services import (
    _s3_enabled,
    _s3_bucket,
    _s3_get_json,
    _s3_put_json,
    TOP4_CACHE_VERSION,
)

BASE_DIR = Path(__file__).resolve().parent.parent
INFO_DIR = BASE_DIR / "data" / "cache" / "top4_player_info" / TOP4_CACHE_VERSION
INFO_DIR.mkdir(parents=True, exist_ok=True)


def _s3_prefix() -> str:
    """Return S3 prefix for cached player info.

    The storage layout no longer uses the seasonal cache version in the S3
    path.  Files must be stored simply as ``top4_player_info/<ID>.json``.  This
    helper therefore returns just the base prefix from the environment
    variable (defaulting to ``top4_player_info``) without appending
    ``TOP4_CACHE_VERSION``.
    """
    base = os.getenv("TOP4_S3_PLAYER_INFO_PREFIX", "top4_player_info")
    return base.rstrip("/")


def _s3_key(pid: int) -> str:
    prefix = _s3_prefix().strip().strip("/")
    return f"{prefix}/{int(pid)}.json"


def load_player_info(pid: int) -> Dict:
    """Load cached info for a Top-4 player (local first, then S3)."""
    p = INFO_DIR / f"{int(pid)}.json"
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(pid)
        if bucket and key:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict):
                fd, tmp = tempfile.mkstemp(prefix="player_info_", suffix=".json", dir=str(INFO_DIR))
                os.close(fd)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, p)
                return data
    return {}


def save_player_info(pid: int, data: Dict) -> None:
    """Persist player info (S3 + local)."""
    payload = data or {}
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(pid)
        if bucket and key and not _s3_put_json(bucket, key, payload):
            print(f"[TOP4:S3] save_player_info fallback pid={pid}")
    fd, tmp = tempfile.mkstemp(prefix="player_info_", suffix=".json", dir=str(INFO_DIR))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INFO_DIR / f"{int(pid)}.json")
