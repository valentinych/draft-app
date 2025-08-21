from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import re
import requests
from bs4 import BeautifulSoup

# === S3 ===
try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:  # boto3 may be missing in some environments
    boto3 = None
    BotoConfig = None

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
LEAGUE_CODES = {
    "EPL": "ENG",
    "Bundesliga": "GER",
    "Serie A": "ITA",
    "La Liga": "SPA",
}
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

"""Utilities for storing state in S3.

The draft state is primarily written to a local JSON file, but when the
environment provides S3 credentials we also try to mirror the file to a bucket.
Previously the code required both ``TOP4_S3_BUCKET`` *and*
``TOP4_S3_STATE_KEY`` environment variables to be defined.  In setups where only
the bucket is configured the state wasn't uploaded, even though we could safely
fall back to a sensible default key.  As a result ``draft_state_top4.json``
remained local and wasn't stored in S3.

To make the behaviour consistent with other services we now default the S3 key
to the file name of ``STATE_FILE``.  ``_s3_enabled`` also uses these helper
functions instead of checking the environment variables directly.
"""


def _s3_enabled() -> bool:
    return bool(_s3_bucket() and _s3_state_key())


def _s3_bucket() -> Optional[str]:
    return os.getenv("TOP4_S3_BUCKET")


def _s3_state_key() -> Optional[str]:
    return os.getenv("TOP4_S3_STATE_KEY", STATE_FILE.name)


def _s3_wishlist_prefix() -> str:
    return os.getenv("TOP4_S3_WISHLIST_PREFIX", "wishlist/top4")


def _s3_players_key() -> str:
    return os.getenv("TOP4_S3_PLAYERS_KEY", "cache/top4_players.json")


def _s3_client():
    if not boto3:
        return None
    cfg = BotoConfig(
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=5,
        read_timeout=8,
    )
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("s3", region_name=region, config=cfg)


def _s3_get_json(bucket: str, key: str) -> Optional[Any]:
    cli = _s3_client()
    if not cli:
        return None
    try:
        obj = cli.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        return json.loads(body.decode("utf-8"))
    except Exception as e:
        print(f"[TOP4:S3] get_object failed: s3://{bucket}/{key} -> {e}")
        return None


def _s3_put_json(bucket: str, key: str, data: Any) -> bool:
    cli = _s3_client()
    if not cli:
        return False
    try:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        cli.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            CacheControl="no-cache",
        )
        return True
    except Exception as e:
        print(f"[TOP4:S3] put_object failed: s3://{bucket}/{key} -> {e}")
        return False


def _build_snake_order(users: List[str], rounds_total: int) -> List[str]:
    order: List[str] = []
    for rnd in range(rounds_total):
        seq = users if rnd % 2 == 0 else list(reversed(users))
        order.extend(seq)
    return order

# ---------- state ----------

def load_state() -> Dict[str, Any]:
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_state_key()
        data = _s3_get_json(bucket, key) if bucket and key else None
        state = data if isinstance(data, dict) else {}
    else:
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
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_state_key()
        if bucket and key and _s3_put_json(bucket, key, state):
            return
        print("[TOP4:S3] save_state fallback to local file")
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
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_players_key()
        data = _s3_get_json(bucket, key) if bucket and key else None
        if isinstance(data, list) and data and data[0].get("fp_last") is not None:
            return data
    data = _json_load(PLAYERS_CACHE)
    if isinstance(data, list) and data and data[0].get("fp_last") is not None:
        return data
    players = _fetch_players()
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_players_key()
        if bucket and key and not _s3_put_json(bucket, key, players):
            print("[TOP4:S3] save_players_cache failed")
    _json_dump_atomic(PLAYERS_CACHE, players)
    return players

# ---------- wishlist ----------
def _wishlist_s3_key(manager: str) -> str:
    safe = manager.replace("/", "_")
    prefix = _s3_wishlist_prefix().strip().strip("/")
    return f"{prefix}/{safe}.json"


def wishlist_load(manager: str) -> List[int]:
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _wishlist_s3_key(manager)
        data = _s3_get_json(bucket, key) if bucket else None
        if isinstance(data, list):
            try:
                return [int(x) for x in data]
            except Exception:
                return []
        if isinstance(data, dict) and "ids" in data:
            try:
                return [int(x) for x in data.get("ids") or []]
            except Exception:
                return []
        return []
    p = WISHLIST_DIR / f"{manager.replace('/', '_')}.json"
    data = _json_load(p)
    if isinstance(data, list):
        try:
            return [int(x) for x in data]
        except Exception:
            return []
    return []


def wishlist_save(manager: str, ids: List[int]) -> None:
    ids_norm = [int(x) for x in ids]
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _wishlist_s3_key(manager)
        if bucket and _s3_put_json(bucket, key, ids_norm):
            return
        print(f"[TOP4:S3] wishlist_save fallback to local for manager={manager}")
    WISHLIST_DIR.mkdir(parents=True, exist_ok=True)
    p = WISHLIST_DIR / f"{manager.replace('/', '_')}.json"
    _json_dump_atomic(p, ids_norm)

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
        pos = POS_CANON.get(pl.get("position")) or pl.get("position")
        if pos in pos_counts:
            pos_counts[pos] += 1
        club = (pl.get("clubName") or "").upper()
        if club:
            club_counts[club] = club_counts.get(club, 0) + 1
        league = pl.get("league")
        if league:
            league_counts[league] = league_counts.get(league, 0) + 1
    for p in players:
        pos = POS_CANON.get(p.get("position")) or p.get("position")
        club = (p.get("clubName") or "").upper()
        league = p.get("league")
        can_pos = pos in TOP4_POSITION_LIMITS and pos_counts.get(pos,0) < TOP4_POSITION_LIMITS[pos]
        can_club = club_counts.get(club,0) < 1 if club else True
        can_league = True
        if league:
            future_league_counts = league_counts.copy()
            future_league_counts[league] = future_league_counts.get(league,0) + 1
            remaining_after = total_slots - (len(roster) + 1)
            required = 0
            for lg in LEAGUES:
                cnt = future_league_counts.get(lg,0)
                if cnt < MIN_PER_LEAGUE:
                    required += MIN_PER_LEAGUE - cnt
            if len(roster) + 1 >= 9 and required > remaining_after:
                can_league = False
        p["canPick"] = bool(can_pos and can_club and can_league)

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
    league_counts: Dict[str, Dict[str, int]] = {}
    for manager, arr in (state.get("rosters") or {}).items():
        g = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        lc = {"ENG": 0, "GER": 0, "ITA": 0, "SPA": 0}
        for entry in arr or []:
            pl = entry.get("player") if isinstance(entry, dict) and entry.get("player") else entry
            pos = POS_CANON.get(pl.get("position")) or pl.get("position")
            if pos in g:
                g[pos].append({
                    "shortName": pl.get("shortName"),
                    "fullName": pl.get("fullName") or pl.get("player_name"),
                    "clubName": pl.get("clubName"),
                    "position": pos,
                })
            league = pl.get("league")
            code = LEAGUE_CODES.get(league or "")
            if code:
                lc[code] = lc.get(code, 0) + 1
        for pos in ("GK", "DEF", "MID", "FWD"):
            need = max(0, slots.get(pos, 0) - len(g[pos]))
            g[pos].extend([None] * need)
        squads_grouped[manager] = g
        league_counts[manager] = lc
    return {
        "title": "Top-4 Draft — Состояние драфта",
        "picks": picks,
        "squads_grouped": squads_grouped,
        "league_counts": league_counts,
        "draft_completed": bool(state.get("draft_completed", False)),
        "next_user": state.get("next_user"),
        "next_round": state.get("next_round"),
        "draft_started_at": state.get("draft_started_at"),
    }
