import json
import os
import re
import tempfile
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

BASE_DIR = Path(__file__).resolve().parent.parent
LINEUP_ROOT = BASE_DIR / 'lineups'
LINEUP_ROOT.mkdir(parents=True, exist_ok=True)
_safe_re = re.compile(r"[^a-z0-9_\-]", re.I)

S3_BUCKET = os.getenv("LINEUP_S3_BUCKET")
S3_PREFIX = os.getenv("LINEUP_S3_PREFIX", "lineups")
_s3_client = None
if S3_BUCKET:
    try:
        _s3_client = boto3.client("s3")
    except Exception:
        _s3_client = None


def _slug(x: str) -> str:
    return _safe_re.sub('_', (x or '').strip().lower()) or 'unknown'


def _file_path(manager: str, gw: int) -> Path:
    p = LINEUP_ROOT / _slug(manager) / f"gw{int(gw)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _s3_key(manager: str, gw: int) -> str:
    prefix = S3_PREFIX.strip("/")
    return f"{prefix}/{_slug(manager)}/gw{int(gw)}.json"


def load_lineup(manager: str, gw: int) -> dict:
    if _s3_client:
        key = _s3_key(manager, gw)
        try:
            obj = _s3_client.get_object(Bucket=S3_BUCKET, Key=key)
            body = obj.get("Body").read().decode("utf-8")
            data = json.loads(body)
            return data if isinstance(data, dict) else {}
        except (ClientError, BotoCoreError, Exception):
            pass
    p = _file_path(manager, gw)
    if not p.exists():
        return {}
    try:
        with p.open('r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_lineup(manager: str, gw: int, payload: dict) -> None:
    data_str = json.dumps(payload, ensure_ascii=False, indent=2)
    if _s3_client:
        key = _s3_key(manager, gw)
        try:
            _s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=key,
                Body=data_str.encode("utf-8"),
                ContentType="application/json",
            )
        except (ClientError, BotoCoreError, Exception):
            pass
    p = _file_path(manager, gw)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix='lineup_', suffix='.json', dir=str(p.parent))
    os.close(tmp_fd)
    with open(tmp_name, 'w', encoding='utf-8') as f:
        f.write(data_str)
    os.replace(tmp_name, p)

