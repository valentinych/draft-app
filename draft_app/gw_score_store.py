import json
import os
import tempfile
from pathlib import Path
from typing import Dict

from .epl_services import _s3_enabled, _s3_bucket, _s3_get_json, _s3_put_json

BASE_DIR = Path(__file__).resolve().parent.parent
GW_SCORE_DIR = BASE_DIR / "data" / "cache" / "gw_scores"
GW_SCORE_DIR.mkdir(parents=True, exist_ok=True)

def _s3_results_prefix() -> str:
    return os.getenv("DRAFT_S3_RESULTS_PREFIX", "gw_scores")

def _s3_key(gw: int) -> str:
    prefix = _s3_results_prefix().strip().strip("/")
    return f"{prefix}/gw{int(gw)}.json"

def load_gw_score(gw: int) -> Dict[str, int]:
    """Load cached total scores for a gameweek."""
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(gw)
        if bucket:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict):
                try:
                    return {str(k): int(v) for k, v in data.items()}
                except Exception:
                    return {}
    p = GW_SCORE_DIR / f"gw{int(gw)}.json"
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
        except Exception:
            pass
    return {}

def save_gw_score(gw: int, scores: Dict[str, int]) -> None:
    """Persist total scores for a gameweek (S3 + local)."""
    payload = {str(k): int(v) for k, v in scores.items()}
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(gw)
        if bucket and not _s3_put_json(bucket, key, payload):
            print(f"[EPL:S3] save_gw_score fallback gw={gw}")
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="gw_score_", suffix=".json", dir=str(GW_SCORE_DIR))
    os.close(tmp_fd)
    with open(tmp_name, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_name, GW_SCORE_DIR / f"gw{int(gw)}.json")
