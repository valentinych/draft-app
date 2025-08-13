import json
import os
import re
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LINEUP_ROOT = BASE_DIR / 'lineups'
LINEUP_ROOT.mkdir(parents=True, exist_ok=True)
_safe_re = re.compile(r"[^a-z0-9_\-]", re.I)


def _slug(x: str) -> str:
    return _safe_re.sub('_', (x or '').strip().lower()) or 'unknown'


def _file_path(manager: str, gw: int) -> Path:
    p = LINEUP_ROOT / _slug(manager) / f"gw{int(gw)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_lineup(manager: str, gw: int) -> dict:
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
    p = _file_path(manager, gw)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix='lineup_', suffix='.json', dir=str(p.parent))
    os.close(tmp_fd)
    with open(tmp_name, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_name, p)

