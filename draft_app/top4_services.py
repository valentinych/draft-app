from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from understat import Understat
import aiohttp

from .config import BASE_DIR, TOP4_USERS, TOP4_POSITION_LIMITS, TOP4_STATE_FILE

BASE = Path(BASE_DIR)
STATE_FILE = Path(TOP4_STATE_FILE)
PLAYERS_CACHE = BASE / "data" / "cache" / "top4_players.json"
WISHLIST_DIR = BASE / "data" / "wishlist" / "top4"

LEAGUES = ["La Liga", "EPL", "Serie A", "Bundesliga"]
POS_CANON = {"GK": "GK", "D": "DEF", "M": "MID", "F": "FWD"}
MIN_PER_LEAGUE = 3

# ---------- helpers ----------

def _json_load(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _json_dump_atomic(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _build_snake_order(users: List[str], rounds_total: int) -> List[str]:
    order: List[str] = []
    for rnd in range(rounds_total):
        seq = users if rnd % 2 == 0 else list(reversed(users))
        order.extend(seq)
    return order

# ---------- state ----------

def load_state() -> Dict[str, Any]:
    state = _json_load(STATE_FILE) or {}
    if not state.get("rosters"):
        state["rosters"] = {u: [] for u in TOP4_USERS}
    if not state.get("draft_order"):
        total = sum(TOP4_POSITION_LIMITS.values())
        state["draft_order"] = _build_snake_order(TOP4_USERS, total)
    if state.get("next_user") is None:
        state["next_user"] = state["draft_order"][0] if state["draft_order"] else None
    return state

def save_state(state: Dict[str, Any]) -> None:
    _json_dump_atomic(STATE_FILE, state)

def who_is_on_clock(state: Dict[str, Any]) -> Optional[str]:
    idx = int(state.get("current_pick_index", 0))
    order = state.get("draft_order", [])
    if 0 <= idx < len(order):
        return order[idx]
    return None

# ---------- players ----------
async def _fetch_players() -> List[Dict[str, Any]]:
    async with aiohttp.ClientSession() as session:
        u = Understat(session)
        players: List[Dict[str, Any]] = []
        for league in LEAGUES:
            data = await u.get_league_players(league, season="2023")
            for p in data:
                players.append({
                    "playerId": int(p["id"]),
                    "fullName": p.get("player_name"),
                    "clubName": p.get("team_title"),
                    "position": p.get("position"),
                    "league": league,
                })
        return players

def load_players() -> List[Dict[str, Any]]:
    data = _json_load(PLAYERS_CACHE)
    if isinstance(data, list) and data:
        return data
    players = asyncio.run(_fetch_players())
    _json_dump_atomic(PLAYERS_CACHE, players)
    return players

# ---------- wishlist ----------
def wishlist_load(manager: str) -> List[int]:
    p = WISHLIST_DIR / f"{manager.replace('/', '_')}.json"
    data = _json_load(p)
    if isinstance(data, list):
        try:
            return [int(x) for x in data]
        except Exception:
            return []
    return []

def wishlist_save(manager: str, ids: List[int]) -> None:
    WISHLIST_DIR.mkdir(parents=True, exist_ok=True)
    p = WISHLIST_DIR / f"{manager.replace('/', '_')}.json"
    _json_dump_atomic(p, [int(x) for x in ids])

# ---------- helpers for picks ----------

def players_index(players: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(p["playerId"]): p for p in players}

def picked_ids_from_state(state: Dict[str, Any]) -> Set[str]:
    picked: Set[str] = set()
    for row in state.get("picks", []):
        pl = row.get("player") or {}
        pid = pl.get("playerId") or row.get("playerId")
        if pid is not None:
            picked.add(str(pid))
    for roster in (state.get("rosters") or {}).values():
        for pl in roster or []:
            pid = pl.get("playerId") or pl.get("id")
            if pid is not None:
                picked.add(str(pid))
    return picked


def annotate_can_pick(players: List[Dict[str, Any]], state: Dict[str, Any], current_user: Optional[str]) -> None:
    if not current_user:
        for p in players: p["canPick"] = False
        return
    on_clock = (state.get("next_user") or who_is_on_clock(state)) == current_user
    draft_completed = bool(state.get("draft_completed", False))
    if draft_completed or not on_clock:
        for p in players: p["canPick"] = False
        return
    roster = (state.get("rosters") or {}).get(current_user, []) or []
    total_slots = sum(TOP4_POSITION_LIMITS.values())
    pos_counts = {"GK":0, "DEF":0, "MID":0, "FWD":0}
    club_counts: Dict[str,int] = {}
    league_counts: Dict[str,int] = {}
    for pl in roster:
        pos = POS_CANON.get(pl.get("position"))
        if pos in pos_counts:
            pos_counts[pos] += 1
        club = (pl.get("clubName") or "").upper()
        if club:
            club_counts[club] = club_counts.get(club, 0) + 1
        league = pl.get("league")
        if league:
            league_counts[league] = league_counts.get(league, 0) + 1
    for p in players:
        pos = POS_CANON.get(p.get("position"))
        club = (p.get("clubName") or "").upper()
        league = p.get("league")
        can = True
        if pos not in TOP4_POSITION_LIMITS:
            can = False
        if can and pos_counts.get(pos,0) >= TOP4_POSITION_LIMITS[pos]:
            can = False
        if can and club_counts.get(club,0) >= 1:
            can = False
        if can and league:
            future_league_counts = league_counts.copy()
            future_league_counts[league] = future_league_counts.get(league,0) + 1
            remaining_after = total_slots - (len(roster) + 1)
            required = 0
            for lg in LEAGUES:
                cnt = future_league_counts.get(lg,0)
                if cnt < MIN_PER_LEAGUE:
                    required += MIN_PER_LEAGUE - cnt
            if len(roster) + 1 >= 9 and required > remaining_after:
                can = False
        p["canPick"] = bool(can)

# ---------- status ----------

def build_status_context() -> Dict[str, Any]:
    state = load_state()
    picks: List[Dict[str, Any]] = []
    for row in state.get("picks", []):
        pl = row.get("player") or {}
        picks.append({
            "round": row.get("round"),
            "user": row.get("user"),
            "player_name": pl.get("fullName"),
            "club": pl.get("clubName"),
            "pos": pl.get("position"),
            "ts": row.get("ts"),
        })
    slots = TOP4_POSITION_LIMITS
    squads_grouped: Dict[str, Dict[str, List[Dict[str, Any] | None]]] = {}
    for manager, arr in (state.get("rosters") or {}).items():
        g = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        for pl in arr or []:
            pos = POS_CANON.get(pl.get("position"))
            if pos in g:
                g[pos].append(pl)
        for pos in g:
            need = max(0, slots.get(pos,0) - len(g[pos]))
            g[pos].extend([None]*need)
        squads_grouped[manager] = g
    return {
        "title": "Top-4 Draft — Состояние драфта",
        "picks": picks,
        "squads_grouped": squads_grouped,
        "draft_completed": bool(state.get("draft_completed", False)),
        "next_user": state.get("next_user"),
        "next_round": state.get("next_round"),
        "draft_started_at": state.get("draft_started_at"),
    }
