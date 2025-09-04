import os
import json
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

from .epl_services import _s3_enabled, _s3_bucket, _s3_get_json, _s3_put_json

BASE_DIR = Path(__file__).resolve().parent.parent
TRANSFERS_DIR = BASE_DIR / "data" / "transfers"
TRANSFERS_DIR.mkdir(parents=True, exist_ok=True)


def _s3_key() -> str:
    return os.getenv("DRAFT_S3_TRANSFERS_KEY", "transfers.json")


def _s3_targets_key() -> str:
    return os.getenv("DRAFT_S3_TRANSFER_TARGETS_KEY", "transfer_targets.json")


def load_transfer_log() -> List[Dict[str, Any]]:
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key()
        if bucket:
            data = _s3_get_json(bucket, key)
            if isinstance(data, list):
                return data
    p = TRANSFERS_DIR / "transfers.json"
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def append_transfer(event: Dict[str, Any]) -> None:
    log = load_transfer_log()
    log.append(event)
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key()
        if bucket and not _s3_put_json(bucket, key, log):
            print("[EPL:S3] append_transfer fallback")
    p = TRANSFERS_DIR / "transfers.json"
    fd, tmp = tempfile.mkstemp(prefix="transfer_", suffix=".json", dir=str(TRANSFERS_DIR))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _targets_path() -> Path:
    return TRANSFERS_DIR / "targets.json"


def load_targets() -> Dict[str, List[int]]:
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_targets_key()
        if bucket:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict):
                return {k: [int(x) for x in v] for k, v in data.items()}
    p = _targets_path()
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {k: [int(x) for x in v] for k, v in data.items()}
        except Exception:
            pass
    return {}


def save_targets(data: Dict[str, List[int]]) -> None:
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_targets_key()
        if bucket and not _s3_put_json(bucket, key, data):
            print("[EPL:S3] save_targets fallback")
    p = _targets_path()
    fd, tmp = tempfile.mkstemp(prefix="targets_", suffix=".json", dir=str(TRANSFERS_DIR))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def pop_transfer_target(manager: str) -> Optional[int]:
    targets = load_targets()
    queue = targets.get(manager) or []
    if not queue:
        return None
    pid = int(queue.pop(0))
    targets[manager] = queue
    save_targets(targets)
    return pid
