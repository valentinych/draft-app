import json
import os
import tempfile
from datetime import datetime, timedelta
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
TOP4_SCORE_DIR = BASE_DIR / "data" / "cache" / "top4_scores" / TOP4_CACHE_VERSION
TOP4_SCORE_DIR.mkdir(parents=True, exist_ok=True)

SCORE_CACHE_TTL = timedelta(minutes=30)


def _s3_prefix() -> str:
    """Return S3 prefix for cached Top-4 scores.

    User requirements changed so cached files should now live directly under
    ``top4_scores/<ID>.json`` without the cache version component.  The
    previous implementation appended ``TOP4_CACHE_VERSION`` to the prefix which
    resulted in paths like ``top4_scores/<version>/<ID>.json``.  To comply with
    the new layout we expose only the base prefix from the environment
    variable (defaulting to ``top4_scores``) and omit the version.
    """
    base = os.getenv("TOP4_S3_SCORES_PREFIX", "top4_scores")
    return base.rstrip("/")


def _s3_key(pid: int) -> str:
    prefix = _s3_prefix().strip().strip("/")
    return f"{prefix}/{int(pid)}.json"


def _fresh(data: Dict, force_refresh: bool = False) -> bool:
    """Check if cached data is fresh. If force_refresh is True, always return False."""
    if force_refresh:
        return False
    ts = data.get("cached_at")
    if not ts:
        return False
    # With TTL removed, cached data is always considered fresh (unless forced refresh)
    return True


def load_top4_score(pid: int, force_refresh: bool = False) -> Dict:
    """Load cached response for a Top-4 player (local first, then S3).
    
    Args:
        pid: Player ID
        force_refresh: If True, ignore cached data and always return empty dict to force refetch
    """
    p = TOP4_SCORE_DIR / f"{int(pid)}.json"
    data = None
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if _fresh(data, force_refresh):
                return data
        except Exception:
            data = None
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(pid)
        if bucket and key:
            s3_data = _s3_get_json(bucket, key)
            if isinstance(s3_data, dict) and _fresh(s3_data, force_refresh):
                tmp_fd, tmp_name = tempfile.mkstemp(prefix="top4_", suffix=".json", dir=str(TOP4_SCORE_DIR))
                os.close(tmp_fd)
                with open(tmp_name, "w", encoding="utf-8") as f:
                    json.dump(s3_data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_name, p)
                return s3_data
    return data or {} if not force_refresh else {}


def save_top4_score(pid: int, data: Dict) -> None:
    """Persist response for a Top-4 player (S3 + local)."""
    payload = dict(data or {})
    payload["cached_at"] = datetime.utcnow().isoformat()
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(pid)
        if bucket and key and not _s3_put_json(bucket, key, payload):
            print(f"[TOP4:S3] save_top4_score fallback pid={pid}")
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="top4_", suffix=".json", dir=str(TOP4_SCORE_DIR))
    os.close(tmp_fd)
    with open(tmp_name, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_name, TOP4_SCORE_DIR / f"{int(pid)}.json")
