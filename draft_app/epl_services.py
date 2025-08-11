from __future__ import annotations
import json, os, tempfile, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime

import requests

# -------- Paths / constants --------
BASE_DIR = Path(__file__).resolve().parent.parent
EPL_STATE = BASE_DIR / "draft_state_epl.json"
EPL_FPL   = BASE_DIR / "players_fpl_bootstrap.json"
WISHLIST_DIR = BASE_DIR / "data" / "wishlist" / "epl"

CACHE_DIR = BASE_DIR / "data" / "cache" / "element_summary"
CACHE_TTL_SEC = 24 * 3600  # 24h

POS_CANON = {
    "Goalkeeper": "GK", "GK": "GK",
    "Defender": "DEF", "DEF": "DEF",
    "Midfielder": "MID", "MID": "MID",
    "Forward": "FWD", "FWD": "FWD",
}
DEFAULT_SLOTS = {"GK": 3, "DEF": 7, "MID": 8, "FWD": 4}
LAST_SEASON = "2024/25"

# -------- JSON I/O --------
def json_load(p: Path) -> Any:
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

def json_dump_atomic(p: Path, data: Any):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="state_", suffix=".json", dir=str(p.parent))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)

# -------- Players (bootstrap) --------
def players_from_fpl(bootstrap: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(bootstrap, dict):
        return out
    elements = bootstrap.get("elements") or []
    teams = {t.get("id"): t.get("name") for t in (bootstrap.get("teams") or [])}
    short = {t.get("id"): (t.get("short_name") or "").upper() for t in (bootstrap.get("teams") or [])}
    pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    for e in elements:
        pid = e.get("id")
        if pid is None: continue
        first = (e.get("first_name") or "").strip()
        second = (e.get("second_name") or "").strip()
        web = (e.get("web_name") or second or "").strip()
        full = f"{first} {second}".strip()
        club_full = teams.get(e.get("team")) or str(e.get("team"))
        club_abbr = short.get(e.get("team")) or (club_full or "").upper()
        out.append({
            "playerId": int(pid),
            "shortName": web,
            "fullName": full,
            "clubName": club_abbr,
            "clubFull": club_full,
            "position": pos_map.get(e.get("element_type")),
            "price": (e.get("now_cost") / 10.0) if isinstance(e.get("now_cost"), (int, float)) else None,
        })
    return out

def players_index(plist: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(p["playerId"]): p for p in plist}

def nameclub_index(plist: List[Dict[str, Any]]) -> Dict[Tuple[str,str], Set[str]]:
    def norm(s: Optional[str]) -> str:
        if not s: return ""
        return " ".join(str(s).replace(".", " ").split()).lower()
    idx: Dict[Tuple[str,str], Set[str]] = {}
    for p in plist:
        pid = str(p["playerId"])
        club = (p.get("clubName") or "").upper()
        for nm in (p.get("shortName"), p.get("fullName")):
            key = (norm(nm), club)
            if not key[0] or not club: continue
            idx.setdefault(key, set()).add(pid)
    return idx

def photo_url_for(pid: int) -> Optional[str]:
    data = json_load(EPL_FPL) or {}
    for e in (data.get("elements") or []):
        if int(e.get("id", -1)) == int(pid):
            code = e.get("code")
            if code:
                return f"https://resources.premierleague.com/premierleague/photos/players/110x140/p{code}.png"
    return None

# -------- State --------
def load_state() -> Dict[str, Any]:
    state = json_load(EPL_STATE) or {}
    state.setdefault("rosters", {})
    state.setdefault("picks", [])
    state.setdefault("draft_order", [])
    state.setdefault("current_pick_index", 0)
    state.setdefault("draft_started_at", None)
    limits = state.setdefault("limits", {})
    limits.setdefault("Max from club", 22)
    return state

def save_state(state: Dict[str, Any]): json_dump_atomic(EPL_STATE, state)

def who_is_on_clock(state: Dict[str, Any]) -> Optional[str]:
    try:
      order = state.get("draft_order") or []
      idx = int(state.get("current_pick_index", 0))
      return order[idx] if 0 <= idx < len(order) else None
    except Exception:
      return None

def slots_from_state(state: Dict[str, Any]) -> Dict[str, int]:
    limits = state.get("limits") or {}
    slots = (limits.get("Slots") if isinstance(limits, dict) else None) or {}
    merged = DEFAULT_SLOTS.copy()
    if isinstance(slots, dict):
        for k, v in slots.items():
            if k in merged and isinstance(v, int) and v >= 0:
                merged[k] = v
    return merged

def picked_fpl_ids_from_state(
    state: Dict[str, Any],
    idx: Dict[Tuple[str,str], Set[str]]
) -> Set[str]:
    def norm(s: Optional[str]) -> str:
        if not s: return ""
        return " ".join(str(s).replace(".", " ").split()).lower()
    picked: Set[str] = set()
    def add(pl: Dict[str, Any]):
        nm  = norm(pl.get("player_name") or pl.get("fullName"))
        club = (pl.get("clubName") or "").upper()
        if nm and club:
            ids = idx.get((nm, club))
            if ids: picked.update(ids)
    for arr in (state.get("rosters") or {}).values():
        if isinstance(arr, list):
            for pl in arr:
                if isinstance(pl, dict): add(pl)
    for row in (state.get("picks") or []):
        pl = (row or {}).get("player") or {}
        if isinstance(pl, dict): add(pl)
    return picked

def annotate_can_pick(players: List[Dict[str, Any]], state: Dict[str, Any], current_user: Optional[str]) -> None:
    if not current_user:
        for p in players: p["canPick"] = False
        return
    draft_completed = bool(state.get("draft_completed", False))
    on_clock = (state.get("next_user") or who_is_on_clock(state)) == current_user
    if draft_completed or not on_clock:
        for p in players: p["canPick"] = False
        return
    roster = (state.get("rosters") or {}).get(current_user, []) or []
    slots = slots_from_state(state)
    max_from_club = (state.get("limits") or {}).get("Max from club", 22)
    pos_counts = {"GK":0, "DEF":0, "MID":0, "FWD":0}
    club_counts: Dict[str,int] = {}
    for pl in roster:
        pos = POS_CANON.get(pl.get("position")) or pl.get("position")
        if pos in pos_counts: pos_counts[pos] += 1
        club = (pl.get("clubName") or "").upper()
        if club: club_counts[club] = club_counts.get(club, 0) + 1
    for p in players:
        pos = p.get("position")
        club = (p.get("clubName") or "").upper()
        can_pos = pos in slots and pos_counts.get(pos, 0) < slots[pos]
        can_club = club_counts.get(club, 0) < max_from_club if club else True
        p["canPick"] = bool(can_pos and can_club)

# -------- element-summary cache --------
def cache_path_for(pid: int) -> Path: return CACHE_DIR / f"{pid}.json"
def cache_valid(p: Path) -> bool:
    if not p.exists(): return False
    try: return (time.time() - p.stat().st_mtime) < CACHE_TTL_SEC
    except Exception: return False

def fetch_element_summary(pid: int) -> Dict[str, Any]:
    p = cache_path_for(pid)
    if cache_valid(p):
        data = json_load(p) or {}
        return data if isinstance(data, dict) else {}
    url = f"https://fantasy.premierleague.com/api/element-summary/{pid}/"
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        data = r.json()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        json_dump_atomic(p, data)
        return data if isinstance(data, dict) else {}
    except Exception:
        return json_load(p) or {}

def fp_last_from_summary(summary: Dict[str, Any]) -> int:
    for row in (summary.get("history_past") or []):
        if (row.get("season_name") or "").strip() == LAST_SEASON:
            try: return int(row.get("total_points") or 0)
            except Exception: return 0
    return 0

# -------- status context --------
def build_status_context() -> Dict[str, Any]:
    state = load_state()
    limits = state.get("limits") or {}

    picks: List[Dict[str, Any]] = []
    for row in state.get("picks", []):
        pl = row.get("player") or {}
        picks.append({
            "round": row.get("round"),
            "user": row.get("user"),
            "player_name": pl.get("player_name") or pl.get("fullName"),
            "club": pl.get("clubName"),
            "pos": POS_CANON.get(pl.get("position")) or pl.get("position"),
            "ts": row.get("ts"),
        })

    slots = slots_from_state(state)
    squads_grouped: Dict[str, Dict[str, List[Dict[str, Any] | None]]] = {}
    for manager, arr in (state.get("rosters") or {}).items():
        g = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        for pl in arr or []:
            pos = POS_CANON.get(pl.get("position")) or pl.get("position")
            if pos in g:
                g[pos].append({
                    "fullName": pl.get("player_name") or pl.get("fullName"),
                    "position": pos,
                    "clubName": pl.get("clubName"),
                })
        for pos in ("GK", "DEF", "MID", "FWD"):
            need = max(0, slots.get(pos, 0) - len(g[pos]))
            g[pos].extend([None] * need)
        squads_grouped[manager] = g

    return {
        "title": "EPL Fantasy Draft — Состояние драфта",
        "limits": limits,
        "picks": picks,
        "squads_grouped": squads_grouped,
        "draft_completed": bool(state.get("draft_completed", False)),
        "next_user": state.get("next_user") or who_is_on_clock(state),
        "next_round": state.get("next_round"),
        "draft_started_at": state.get("draft_started_at"),
    }

# -------- wishlist storage --------
def wishlist_path(manager: str) -> Path:
    safe = manager.replace("/", "_")
    return WISHLIST_DIR / f"{safe}.json"

def wishlist_load(manager: str) -> List[int]:
    p = wishlist_path(manager)
    try:
        data = json_load(p)
        if isinstance(data, list):
            return [int(x) for x in data]
    except Exception:
        pass
    return []

def wishlist_save(manager: str, ids: List[int]) -> None:
    WISHLIST_DIR.mkdir(parents=True, exist_ok=True)
    json_dump_atomic(wishlist_path(manager), [int(x) for x in ids])
