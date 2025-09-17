import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import requests

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except Exception:  # pragma: no cover - boto3 may be missing locally
    boto3 = None
    BotoCoreError = ClientError = Exception

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "ucl_stat"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TTL = timedelta(hours=12)
S3_BUCKET = os.getenv("DRAFT_S3_BUCKET")
S3_PREFIX = os.getenv("UCL_S3_STATS_PREFIX", "ucl_stat")


def _s3_enabled() -> bool:
    return bool(S3_BUCKET)


def _s3_key(player_id: int) -> str:
    prefix = S3_PREFIX.strip().strip("/")
    return f"{prefix}/{int(player_id)}.json"


def _s3_client():
    if not boto3:
        return None
    try:
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        return boto3.client("s3", region_name=region)
    except Exception:
        return None


def _load_local(player_id: int) -> Optional[Dict]:
    path = CACHE_DIR / f"{int(player_id)}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_local(player_id: int, payload: Dict) -> None:
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="ucl_stat_", suffix=".json", dir=str(CACHE_DIR))
        os.close(fd)
        with open(tmp_name, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, CACHE_DIR / f"{int(player_id)}.json")
    except Exception:
        pass


def _load_s3(player_id: int) -> Optional[Dict]:
    if not _s3_enabled():
        return None
    client = _s3_client()
    if not client:
        return None
    key = _s3_key(player_id)
    try:
        obj = client.get_object(Bucket=S3_BUCKET, Key=key)
        body = obj["Body"].read().decode("utf-8")
        return json.loads(body)
    except (ClientError, BotoCoreError, Exception):
        return None


def _save_s3(player_id: int, payload: Dict) -> None:
    if not _s3_enabled():
        return
    client = _s3_client()
    if not client:
        return
    key = _s3_key(player_id)
    try:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        client.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            CacheControl="max-age=0, no-cache",
        )
    except (ClientError, BotoCoreError, Exception):
        pass


def _fresh(payload: Optional[Dict]) -> bool:
    if not payload:
        return False
    ts = payload.get("cached_at")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts)
    except Exception:
        return False
    return datetime.utcnow() - cached < TTL


def _fetch_remote(player_id: int) -> Optional[Dict]:
    url = f"https://gaming.uefa.com/en/uclfantasy/services/feeds/popupstats/popupstats_80_{int(player_id)}.json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def get_player_stats(player_id: int) -> Dict:
    """Return cached popup stats for a player, updating if stale."""
    cached = _load_local(player_id)
    if not _fresh(cached):
        s3_payload = _load_s3(player_id)
        if _fresh(s3_payload):
            cached = s3_payload
            _save_local(player_id, cached)
        else:
            remote = _fetch_remote(player_id)
            if remote is not None:
                cached = {
                    "cached_at": datetime.utcnow().isoformat(),
                    "data": remote,
                }
                _save_local(player_id, cached)
                _save_s3(player_id, cached)
            elif s3_payload:
                cached = s3_payload
                _save_local(player_id, cached)
    return (cached or {}).get("data", {})
