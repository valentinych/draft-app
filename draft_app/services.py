import json
import os
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from .config import (
    UCL_POSITION_MAP, FPL_POSITION_MAP, WARSZAWA_TZ,
    EPL_PLAYERS_FILE
)

# HTTP session с retry
session_req = requests.Session()
retry_strategy = Retry(total=3, backoff_factor=1,
                       status_forcelist=[429, 500, 502, 503, 504],
                       allowed_methods=["GET"])
adapter = HTTPAdapter(max_retries=retry_strategy)
session_req.mount('https://', adapter)
session_req.mount('http://', adapter)

HEADERS_GENERIC = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Accept': 'application/json, text/plain, */*'
}

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

# ---------- UCL players ----------
def parse_ucl_players(data):
    plist = data.get('data', {}).get('value', {}).get('playerList', [])
    players = []
    for p in plist:
        try:
            pid = int(p['id'])
            skill = int(p['skill'])
        except Exception:
            continue
        players.append({
            'playerId': pid,
            'fullName': p.get('pDName', '').strip(),
            'clubName': p.get('cCode', '').strip(),
            'position': UCL_POSITION_MAP.get(skill, 'Midfielder')
        })
    return players

# ---------- EPL bootstrap ----------
_last_bootstrap = None

def fetch_and_cache_fpl_bootstrap():
    global _last_bootstrap
    url = "https://fantasy.premierleague.com/api/bootstrap-static/"
    try:
        resp = session_req.get(url, headers=HEADERS_GENERIC, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        save_json(EPL_PLAYERS_FILE, data)
        _last_bootstrap = data
        return data
    except Exception:
        return None

def get_bootstrap_data():
    global _last_bootstrap
    if _last_bootstrap is not None:
        return _last_bootstrap
    data = load_json(EPL_PLAYERS_FILE)
    if data is not None:
        _last_bootstrap = data
        return _last_bootstrap
    return fetch_and_cache_fpl_bootstrap()

def load_epl_players():
    data = get_bootstrap_data()
    if not data:
        return []
    teams = {t['id']: (t.get('short_name') or t.get('name')) for t in data.get('teams', [])}
    elements = data.get('elements', [])
    players = []
    for e in elements:
        try:
            pid = int(e['id'])
            pos = int(e['element_type'])  # 1 GK, 2 DEF, 3 MID, 4 FWD
            team_id = int(e['team'])
        except Exception:
            continue
        name = e.get('web_name') or (e.get('first_name', '') + ' ' + e.get('second_name', '')).strip()
        price_val = e.get('now_cost', 0)  # десятые доли млн
        try:
            price = float(price_val) / 10.0
        except Exception:
            price = 0.0
        players.append({
            'playerId': pid,
            'fullName': name,
            'clubName': teams.get(team_id, str(team_id)),
            'position': FPL_POSITION_MAP.get(pos, 'Midfielder'),
            'price': price
        })
    return players

def format_deadline(dt_iso_str):
    if not dt_iso_str:
        return ''
    iso = dt_iso_str.replace('Z', '+00:00')
    dt_utc = datetime.fromisoformat(iso)
    return dt_utc.astimezone(WARSZAWA_TZ).strftime('%d %b %H:%M')

def epl_deadlines_window():
    """
    Возвращает (rounds_range, gw_deadlines), где rounds_range — список GW:
    [центр-5 .. центр+5] (в пределах 1..34).
    gw_deadlines — dict gw -> 'dd Mon HH:MM'
    """
    data = get_bootstrap_data()
    events = data.get('events', []) if data else []
    events = [e for e in events if int(e.get('id', 0)) <= 34]
    if not events:
        return [1], {}
    center = None
    for ev in events:
        if ev.get('is_next'):
            center = int(ev['id'])
            break
    if center is None:
        for ev in events:
            if ev.get('is_current'):
                center = int(ev['id'])
                break
    if center is None:
        events_sorted = sorted(events, key=lambda x: x.get('deadline_time', '9999'))
        center = int(events_sorted[0]['id']) if events_sorted else 1
    start = max(1, center - 5)
    end   = min(34, center + 5)
    rng = list(range(start, end + 1))
    deadlines = {}
    for ev in events:
        gw = int(ev['id'])
        if gw in rng:
            deadlines[gw] = format_deadline(ev.get('deadline_time'))
    return rng, deadlines
