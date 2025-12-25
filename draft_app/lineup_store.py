import json
import os
import re
import tempfile
import unicodedata
import hashlib
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

BASE_DIR = Path(__file__).resolve().parent.parent
LINEUP_ROOT = BASE_DIR / 'lineups'
LINEUP_ROOT.mkdir(parents=True, exist_ok=True)
_safe_re = re.compile(r"[^a-z0-9_\-]", re.I)

S3_BUCKET = os.getenv("LINEUP_S3_BUCKET") or os.getenv("DRAFT_S3_BUCKET")
S3_PREFIX = os.getenv("LINEUP_S3_PREFIX") or os.getenv("DRAFT_S3_LINEUPS_PREFIX", "lineups")
_s3_client = None
if S3_BUCKET:
    try:
        _s3_client = boto3.client("s3")
    except Exception:
        _s3_client = None

def _slug_parts(manager: str) -> tuple[str, str, bool]:
    raw = (manager or '').strip()
    norm = unicodedata.normalize('NFKD', raw)
    ascii_norm = norm.encode('ascii', 'ignore').decode('ascii')
    ascii_slug = _safe_re.sub('_', ascii_norm.lower()).strip('_')
    has_ascii = bool(ascii_slug)
    if not ascii_slug:
        ascii_slug = 'user'
    digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8] if raw else ''
    slug = f"{ascii_slug}_{digest}" if digest else ascii_slug
    legacy = _safe_re.sub('_', raw.lower()) or 'unknown'
    return slug, legacy, has_ascii


def _file_path(manager: str, gw: int) -> Path:
    slug, _, _ = _slug_parts(manager)
    p = LINEUP_ROOT / slug / f"gw{int(gw)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _legacy_file_path(manager: str, gw: int) -> Path:
    _, legacy, _ = _slug_parts(manager)
    p = LINEUP_ROOT / legacy / f"gw{int(gw)}.json"
    return p


def _s3_key(manager: str, gw: int) -> str:
    slug, _, _ = _slug_parts(manager)
    prefix = S3_PREFIX.strip("/")
    return f"{prefix}/{slug}/gw{int(gw)}.json"


def load_lineup(manager: str, gw: int, prefer_s3: bool = True) -> dict:
    """Загружает состав менеджера для указанного GW
    
    Args:
        manager: Имя менеджера
        gw: Номер gameweek
        prefer_s3: Если True, приоритетно загружает из S3 (по умолчанию True)
    
    Returns:
        Словарь с составом или пустой словарь, если не найден
    """
    slug, _, has_ascii = _slug_parts(manager)
    
    # Приоритетно загружаем из S3, если доступен
    if prefer_s3 and _s3_client and S3_BUCKET:
        key = _s3_key(manager, gw)
        try:
            obj = _s3_client.get_object(Bucket=S3_BUCKET, Key=key)
            body = obj.get("Body").read().decode("utf-8")
            data = json.loads(body)
            if isinstance(data, dict):
                return data
        except ClientError as e:
            # Если файл не найден (404), это нормально - пробуем локальные файлы
            if e.response.get('Error', {}).get('Code') != 'NoSuchKey':
                pass  # Другие ошибки игнорируем
        except (BotoCoreError, Exception):
            pass
    
    # Пробуем загрузить из локальных файлов
    p = _file_path(manager, gw)
    if p.exists():
        try:
            with p.open('r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    
    # Пробуем legacy путь
    if has_ascii:
        legacy = _legacy_file_path(manager, gw)
        if legacy.exists():
            try:
                with legacy.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception:
                pass
    
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
    legacy = _legacy_file_path(manager, gw)
    if legacy != p and legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass


def remove_lineup(manager: str, gw: int) -> None:
    slug, legacy, _ = _slug_parts(manager)
    slug_path = LINEUP_ROOT / slug / f"gw{int(gw)}.json"
    legacy_path = LINEUP_ROOT / legacy / f"gw{int(gw)}.json"
    for path in {slug_path, legacy_path}:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    if _s3_client:
        key = _s3_key(manager, gw)
        try:
            _s3_client.delete_object(Bucket=S3_BUCKET, Key=key)
        except (ClientError, BotoCoreError, Exception):
            pass
