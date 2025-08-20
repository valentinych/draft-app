from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import re
import requests
from bs4 import BeautifulSoup

from .config import BASE_DIR, TOP4_USERS, TOP4_POSITION_LIMITS, TOP4_STATE_FILE

BASE = Path(BASE_DIR)
STATE_FILE = Path(TOP4_STATE_FILE)
PLAYERS_CACHE = BASE / "data" / "cache" / "top4_players.json"
WISHLIST_DIR = BASE / "data" / "wishlist" / "top4"

LEAGUE_TOURNAMENTS = {
    # IDs of tournaments on fantasy-h2h.ru for 2025 season
    "La Liga": 315,
    "EPL": 316,
    "Serie A": 318,
    "Bundesliga": 314,
}
# IDs for 2024 season to fetch last season FP (Pts)
LEAGUE_TOURNAMENTS_2024 = {
    "La Liga": 286,
    "EPL": 287,
    "Serie A": 288,
    "Bundesliga": 290,
}
LEAGUES = list(LEAGUE_TOURNAMENTS.keys())
POS_CANON = {"GK": "GK", "D": "DEF", "M": "MID", "F": "FWD"}
MIN_PER_LEAGUE = 3
HEADERS = {"User-Agent": "Mozilla/5.0"}
POS_MAP_RUS = {"Вр": "GK", "Зщ": "DEF", "Пз": "MID", "Нп": "FWD"}

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

def _fetch_team_ids(tournament_id: int) -> List[int]:
    """Return list of team ids for given tournament."""
    url = f"https://fantasy-h2h.ru/analytics/fantasy_players_statistics/{tournament_id}"
    try:
        resp = requests.get(url, params={"ajax": 1, "offset": 0, "limit": 1}, headers=HEADERS)
        html = resp.json().get("data", "")
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    opts = soup.select('select[name="filter[sport_team_id]"] option')
    ids: List[int] = []
    for opt in opts:
        val = opt.get("value")
        if not val or val == "0":
            continue
        try:
            ids.append(int(val))
        except Exception:
            continue
    return ids


def _fetch_league_players(tournament_id: int, league: str) -> List[Dict[str, Any]]:
    players: List[Dict[str, Any]] = []
    url = f"https://fantasy-h2h.ru/analytics/fantasy_players_statistics/{tournament_id}"
    team_ids = _fetch_team_ids(tournament_id)
    seen: Set[int] = set()
    for team_id in team_ids:
        offset = 0
        while True:
            params = {"ajax": 1, "offset": offset, "limit": 100, "filter[sport_team_id]": team_id}
            resp = requests.get(url, params=params, headers=HEADERS)
            try:
                html = resp.json().get("data", "")
            except Exception:
                break
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table#players_list tbody tr")
            if not rows:
                break
            for row in rows:
                cols = row.select("td")
                if len(cols) < 6:
                    continue
                pos_rus = cols[1].get_text(strip=True)
                club = cols[2].get_text(strip=True)
                name = cols[3].get_text(strip=True)
                price_txt = cols[4].get_text(strip=True).replace(",", ".")
                pop_txt = cols[5].get_text(strip=True).replace(",", ".")
                try:
                    price = float(price_txt) if price_txt else 0.0
                except Exception:
                    price = 0.0
                try:
                    popularity = float(pop_txt) if pop_txt else 0.0
                except Exception:
                    popularity = 0.0
                link = cols[-1].find("a", class_="tooltipster uname")
                pid = None
                if link:
                    m = re.search(r"/player/(\d+)", link.get("data-tooltip_url", ""))
                    if m:
                        pid = int(m.group(1))
                if pid is None or pid in seen:
                    continue
                players.append({
                    "playerId": pid,
                    "fullName": name,
                    "clubName": club,
                    "position": POS_MAP_RUS.get(pos_rus, pos_rus),
                    "league": league,
                    "price": price,
                    "popularity": popularity,
                })
                seen.add(pid)
            offset += len(rows)
    return players


def _fetch_prev_fp(tournament_id: int) -> Dict[str, float]:
    """Fetch last season FP (Pts) for given tournament id."""
    fp: Dict[str, float] = {}
    url = f"https://fantasy-h2h.ru/analytics/fantasy_players_statistics/{tournament_id}"
    team_ids = _fetch_team_ids(tournament_id)
    for team_id in team_ids:
        offset = 0
        while True:
            params = {"ajax": 1, "offset": offset, "limit": 100, "filter[sport_team_id]": team_id}
            resp = requests.get(url, params=params, headers=HEADERS)
            try:
                html = resp.json().get("data", "")
            except Exception:
                break
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table#players_list tbody tr")
            if not rows:
                break
            for row in rows:
                cols = row.select("td")
                if len(cols) < 15:
                    continue
                club = cols[2].get_text(strip=True)
                name = cols[3].get_text(strip=True)
                pts_txt = cols[14].get_text(strip=True).replace(",", ".")
                try:
                    pts = float(pts_txt) if pts_txt else 0.0
                except Exception:
                    pts = 0.0
                key = f"{club}|{name}"
                fp[key] = pts
            offset += len(rows)
    return fp

def _fetch_players() -> List[Dict[str, Any]]:
    players_map: Dict[int, Dict[str, Any]] = {}
    prev_fp: Dict[str, float] = {}
    for league, tid in LEAGUE_TOURNAMENTS_2024.items():
        prev_fp.update(_fetch_prev_fp(tid))
    for league, tid in LEAGUE_TOURNAMENTS.items():
        league_players = _fetch_league_players(tid, league)
        for p in league_players:
            key = f"{p.get('clubName')}|{p.get('fullName')}"
            p["fp_last"] = prev_fp.get(key, 0.0)
            players_map[p["playerId"]] = p
    return list(players_map.values())

def load_players() -> List[Dict[str, Any]]:
    data = _json_load(PLAYERS_CACHE)
    if isinstance(data, list) and data and data[0].get("fp_last") is not None:
        return data
    players = _fetch_players()
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
