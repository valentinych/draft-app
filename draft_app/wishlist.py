import os
import json
import tempfile
import re
from pathlib import Path
from flask import Blueprint, request, jsonify, session

bp = Blueprint('wishlist', __name__)

BASE_DIR = Path(__file__).resolve().parent.parent
WISHLIST_ROOT = Path(os.path.join(BASE_DIR, 'wishlists'))
WISHLIST_ROOT.mkdir(parents=True, exist_ok=True)
_safe_re = re.compile(r"[^a-z0-9_\-]", re.I)

def _slug(x: str) -> str:
    return _safe_re.sub('_', (x or '').strip().lower()) or 'unknown'

def _path_for(league: str, manager: str) -> Path:
    p = WISHLIST_ROOT / (league or 'epl') / f"{_slug(manager)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _read_ids(p: Path) -> list[str]:
    if not p.exists():
        return []
    try:
        with p.open('r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and 'ids' in data:
            return [str(x) for x in data['ids']]
        return [str(x) for x in (data or [])]
    except Exception:
        return []

def _write_ids(p: Path, ids: list[str]):
    tmp_fd, tmp_name = tempfile.mkstemp(prefix='wishlist_', suffix='.json', dir=str(p.parent))
    os.close(tmp_fd)
    with open(tmp_name, 'w', encoding='utf-8') as f:
        json.dump({'ids': sorted(list({str(x) for x in ids}))}, f, ensure_ascii=False, indent=2)
    os.replace(tmp_name, p)

@bp.get('/api/wishlist')
def get_wishlist():
    league = (request.args.get('league') or 'epl').strip().lower()
    manager = (request.args.get('manager') or session.get('user_name') or '').strip()
    if not manager:
        return jsonify({'ids': []})
    p = _path_for(league, manager)
    return jsonify({'ids': _read_ids(p)})

@bp.put('/api/wishlist')
def put_wishlist():
    payload = request.get_json(silent=True) or {}
    league = (payload.get('league') or 'epl').strip().lower()
    manager = (payload.get('manager') or session.get('user_name') or '').strip()
    add = [str(x) for x in (payload.get('add') or [])]
    remove = [str(x) for x in (payload.get('remove') or [])]

    if not manager:
        return jsonify({'error': 'manager is required'}), 400

    p = _path_for(league, manager)
    ids = set(_read_ids(p))
    ids |= set(add)
    ids -= set(remove)
    _write_ids(p, sorted(list(ids)))
    return jsonify({'ids': sorted(list(ids))})
