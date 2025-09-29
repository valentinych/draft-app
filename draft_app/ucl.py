from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    jsonify,
)
import os
import threading
try:
    import boto3
except Exception:
    boto3 = None

from .ucl_stats_store import (
    get_current_matchday,
    get_current_matchday_cached,
    get_player_stats,
    get_player_stats_cached,
    refresh_players_batch,
)

bp = Blueprint("ucl", __name__)

_STATS_REFRESH_STATE: Dict[str, Any] = {
    "running": False,
    "started": None,
    "summary": None,
    "error": None,
    "finished": None,
}
_STATS_REFRESH_LOCK = threading.Lock()

# --- файлы данных (подгони пути под свой проект при необходимости) ---
BASE_DIR = Path(__file__).resolve().parent.parent
UCL_STATE = BASE_DIR / "draft_state_ucl.json"
UCL_PLAYERS = BASE_DIR / "players_80_en_1.json"  # актуальный список игроков
UCL_POINTS = BASE_DIR / "players_70_en_3.json"   # очки прошлого сезона

# --- параметры UCL драфта ---
UCL_PARTICIPANTS = ["Ксана", "Андрей", "Саша", "Руслан", "Женя", "Макс", "Серёга Б", "Сергей"]
UCL_ROUNDS = 25

# ----------------- helpers -----------------
def _json_load(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None

def _json_dump_atomic(p: Path, data: Any) -> None:
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass

# Optional S3-backed state for UCL
def _ucl_s3_enabled() -> bool:
    """Return True when UCL state should be synchronised with S3."""
    return bool(_ucl_s3_bucket())

def _ucl_s3_bucket() -> Optional[str]:
    return os.getenv("DRAFT_S3_BUCKET")

def _ucl_s3_key() -> Optional[str]:
    key = os.getenv("DRAFT_S3_UCL_STATE_KEY")
    if key:
        return key.strip()
    legacy = os.getenv("UCL_S3_STATE_KEY")
    if legacy:
        return legacy.strip()
    generic = os.getenv("DRAFT_S3_STATE_KEY")
    if generic:
        g = generic.strip()
        if "ucl" in g.lower():
            return g
    return "prod/draft_state_ucl.json"

def _ucl_s3_client():
    if not boto3:
        return None
    try:
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        return boto3.client("s3", region_name=region)
    except Exception:
        return None

def _ucl_state_load() -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    if _ucl_s3_enabled():
        cli = _ucl_s3_client()
        bucket = _ucl_s3_bucket()
        key = _ucl_s3_key()
        try:
            if cli and bucket and key:
                obj = cli.get_object(Bucket=bucket, Key=key)
                body = obj["Body"].read().decode("utf-8")
                state = json.loads(body) or {}
        except Exception:
            state = {}
    if not state:
        state = _json_load(UCL_STATE) or {}
    state.setdefault("rosters", {})
    state.setdefault("picks", [])
    state.setdefault("draft_order", [])
    state.setdefault("current_pick_index", 0)
    state.setdefault("next_user", None)
    state.setdefault("next_round", 1)
    return state

def _ucl_state_save(state: Dict[str, Any]) -> None:
    _json_dump_atomic(UCL_STATE, state)
    if not _ucl_s3_enabled():
        return
    cli = _ucl_s3_client()
    bucket = _ucl_s3_bucket()
    key = _ucl_s3_key()
    try:
        if cli and bucket and key:
            body = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
            cli.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json; charset=utf-8", CacheControl="no-cache")
    except Exception:
        pass

def _players_from_ucl(raw: Any) -> List[Dict[str, Any]]:
    """
    Унификация списка игроков UCL JSON -> [{playerId, fullName, clubName, position, price}, ...]
    В players_70_en_3.json обычно список словарей с нужными полями.
    """
    out: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for p in raw:
            pid = p.get("playerId") or p.get("id") or p.get("pid")
            if pid is None:
                continue
            out.append(
                {
                    "playerId": int(pid),
                    "fullName": p.get("fullName") or p.get("name"),
                    "clubName": p.get("clubName") or p.get("club") or p.get("team"),
                    "position": p.get("position") or p.get("pos"),
                    "price": p.get("price") if isinstance(p.get("price"), (int, float)) else (
                        p.get("value") if isinstance(p.get("value"), (int, float)) else None
                    ),
                }
            )
    elif isinstance(raw, dict) and isinstance(raw.get("players"), list):
        for p in raw["players"]:
            pid = p.get("playerId") or p.get("id")
            if pid is None:
                continue
            out.append(
                {
                    "playerId": int(pid),
                    "fullName": p.get("fullName") or p.get("name"),
                    "clubName": p.get("clubName") or p.get("team"),
                    "position": p.get("position"),
                    "price": p.get("price") if isinstance(p.get("price"), (int, float)) else (
                        p.get("value") if isinstance(p.get("value"), (int, float)) else None
                    ),
                }
            )
    elif isinstance(raw, dict):
        # Support structure like: {"data": {"value": {"playerList": [...]}}}
        players = (
            raw.get("data", {})
               .get("value", {})
               .get("playerList", [])
            if isinstance(raw.get("data"), dict) else []
        )
        if isinstance(players, list):
            for p in players:
                try:
                    pid = int(p.get("id")) if p.get("id") is not None else None
                except Exception:
                    pid = None
                if pid is None:
                    continue
                # Map UCL feed fields to our canonical ones
                skill = p.get("skill")
                pos = None
                if isinstance(skill, int):
                    pos = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(skill)
                out.append(
                    {
                        "playerId": pid,
                        "fullName": p.get("pFName") or p.get("pDName") or p.get("latinName"),
                        "shortName": p.get("pDName"),
                        "clubName": p.get("tName") or p.get("tSCode") or p.get("cCode"),
                        "position": pos,
                        "price": p.get("value") if isinstance(p.get("value"), (int, float)) else None,
                        # Optional status-like fields for UI
                        "status": p.get("pStatus") or p.get("qStatus") or "",
                        "news": p.get("trained") or "",
                        # Current season points from UEFA feed
                        "curGDPts": p.get("curGDPts", 0),
                        # Assume can pick by default; server can refine later
                        "canPick": True,
                    }
                )
    return out


def _ensure_fp_current_from_uefa_feed(players: List[Dict[str, Any]]) -> None:
    """Ensure all players have fp_current set from curGDPts (UEFA feed)"""
    for player in players:
        if isinstance(player, dict):
            player["fp_current"] = int(player.get("curGDPts", 0) or 0)

def _ucl_points_map(raw: Any) -> Dict[int, int]:
    """Extract mapping playerId -> total points from raw JSON."""
    mapping: Dict[int, int] = {}
    plist = []
    if isinstance(raw, dict):
        plist = raw.get("data", {}).get("value", {}).get("playerList", [])
    if isinstance(plist, list):
        for p in plist:
            try:
                pid = int(p.get("id"))
                pts = int(p.get("totPts", 0))
            except Exception:
                continue
            mapping[pid] = pts
    return mapping

def _uniq_sorted(values: List[str]) -> List[str]:
    return sorted({v for v in values if v})

def _apply_filters(players: List[Dict[str, Any]], club: str, pos: str) -> List[Dict[str, Any]]:
    if club:
        players = [p for p in players if (p.get("clubName") or "") == club]
    if pos:
        players = [p for p in players if (p.get("position") or "") == pos]
    return players

def _picked_ids_from_state(state: Dict[str, Any]) -> Set[str]:
    picked: Set[str] = set()
    for arr in (state.get("rosters") or {}).values():
        if isinstance(arr, list):
            for pl in arr:
                pid = pl.get("playerId") or pl.get("id") or pl.get("pid")
                if pid is not None:
                    picked.add(str(pid))
    for row in state.get("picks", []) or []:
        pid = (row or {}).get("playerId") or (row or {}).get("id") or ((row or {}).get("player") or {}).get("playerId")
        if pid is not None:
            picked.add(str(pid))
    return picked


def _collect_player_ids_for_stats(state: Dict[str, Any]) -> List[int]:
    seen: Set[int] = set()

    for roster in (state.get("rosters") or {}).values():
        if not isinstance(roster, list):
            continue
        for entry in roster:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("playerId") or entry.get("id") or entry.get("pid")
            if pid is None:
                continue
            try:
                seen.add(int(pid))
            except Exception:
                continue

    for pick in state.get("picks") or []:
        if not isinstance(pick, dict):
            continue
        pid = pick.get("playerId") or pick.get("id") or (pick.get("player") or {}).get("playerId")
        if pid is None:
            continue
        try:
            seen.add(int(pid))
        except Exception:
            continue

    return sorted(seen)


def _stats_refresh_running() -> bool:
    with _STATS_REFRESH_LOCK:
        return bool(_STATS_REFRESH_STATE.get("running"))


def _consume_stats_refresh_result() -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[datetime]]:
    with _STATS_REFRESH_LOCK:
        summary = _STATS_REFRESH_STATE.get("summary")
        error = _STATS_REFRESH_STATE.get("error")
        finished = _STATS_REFRESH_STATE.get("finished")
        if summary is None and error is None:
            return (None, None, None)
        _STATS_REFRESH_STATE["summary"] = None
        _STATS_REFRESH_STATE["error"] = None
        _STATS_REFRESH_STATE["finished"] = None
    return (summary, error, finished)


def _start_stats_refresh_job(app, player_ids: List[int]) -> bool:
    with _STATS_REFRESH_LOCK:
        if _STATS_REFRESH_STATE.get("running"):
            print("[ucl:refresh] job already running", flush=True)
            return False
        _STATS_REFRESH_STATE["running"] = True
        _STATS_REFRESH_STATE["started"] = datetime.utcnow()
        _STATS_REFRESH_STATE["summary"] = None
        _STATS_REFRESH_STATE["error"] = None
        _STATS_REFRESH_STATE["finished"] = None

    print(f"[ucl:refresh] scheduling background job players={len(player_ids)}", flush=True)
    thread = threading.Thread(target=_stats_refresh_worker, args=(app, list(player_ids)), daemon=True)
    thread.start()
    return True


def _stats_refresh_worker(app, player_ids: List[int]) -> None:
    try:
        summary = refresh_players_batch(player_ids)
        error = None
        app.logger.info(
            "[UCL] popup stats refresh finished: %s total, %s failures",
            summary.get("total"),
            summary.get("failures"),
        )
        print(
            f"[ucl:refresh] worker finished total={summary.get('total')} failures={summary.get('failures')}",
            flush=True,
        )
    except Exception as exc:
        summary = None
        error = str(exc)
        app.logger.exception("[UCL] popup stats refresh failed")
        print(f"[ucl:refresh] worker exception={exc}", flush=True)

    with _STATS_REFRESH_LOCK:
        _STATS_REFRESH_STATE["running"] = False
        _STATS_REFRESH_STATE["summary"] = summary
        _STATS_REFRESH_STATE["error"] = error
        _STATS_REFRESH_STATE["finished"] = datetime.utcnow()


def _flash_stats_refresh_result() -> Optional[Dict[str, Any]]:
    summary, refresh_error, _finished = _consume_stats_refresh_result()
    if summary:
        total = int(summary.get("total") or 0)
        failures = int(summary.get("failures") or 0)
        success = max(0, total - failures)
        level = "success" if failures == 0 else "warning"
        flash(f"Обновление статистики завершено: {success}/{total} игроков", level)
        return summary
    if refresh_error:
        flash(f"Обновление статистики завершилось ошибкой: {refresh_error}", "danger")
    return summary


def _ucl_matchday_from_state_only(state: Dict[str, Any]) -> int:
    try:
        md = int(state.get("next_round") or 0)
    except Exception:
        md = 0
    if md <= 0:
        cached_md = get_current_matchday_cached()
        if cached_md:
            return cached_md
        try:
            md = int(state.get("current_pick_index") or 0)
        except Exception:
            md = 0
    if md <= 0:
        return 1
    return max(1, md)

UCL_SLOTS_DEFAULT = {"GK": 3, "DEF": 8, "MID": 9, "FWD": 5}
UCL_MAX_FROM_CLUB_DEFAULT = 1

def _slots_from_state(state: Dict[str, Any]) -> Dict[str, int]:
    limits = state.get("limits") or {}
    slots = (limits.get("Slots") if isinstance(limits, dict) else None) or {}
    merged = UCL_SLOTS_DEFAULT.copy()
    if isinstance(slots, dict):
        for k, v in slots.items():
            if k in merged and isinstance(v, int) and v >= 0:
                merged[k] = v
    return merged

def _max_from_club(state: Dict[str, Any]) -> int:
    try:
        return int((state.get("limits") or {}).get("Max from club", UCL_MAX_FROM_CLUB_DEFAULT))
    except Exception:
        return UCL_MAX_FROM_CLUB_DEFAULT

def _who_is_on_clock(state: Dict[str, Any]) -> Optional[str]:
    # Respect explicit turn control if present
    try:
        left = int(state.get("turn_left", 0))
        tu = state.get("turn_user")
        if tu and left > 0:
            return tu
    except Exception:
        pass
    try:
        idx = int(state.get("current_pick_index", 0))
        order = state.get("draft_order") or []
        if 0 <= idx < len(order):
            return order[idx]
    except Exception:
        pass
    return state.get("next_user")

def _annotate_can_pick_ucl(players: List[Dict[str, Any]], state: Dict[str, Any], current_user: Optional[str]) -> None:
    if not current_user:
        for p in players:
            p["canPick"] = False
        return
    draft_completed = bool(state.get("draft_completed", False))
    on_clock = (_who_is_on_clock(state) == current_user)
    if draft_completed or not on_clock:
        for p in players:
            p["canPick"] = False
        return
    roster = (state.get("rosters") or {}).get(current_user, []) or []
    slots = _slots_from_state(state)
    max_from_club = _max_from_club(state)
    pos_counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    club_counts: Dict[str, int] = {}
    for pl in roster:
        pos = pl.get("position")
        if pos in pos_counts:
            pos_counts[pos] += 1
        club = (pl.get("clubName") or "").upper()
        if club:
            club_counts[club] = club_counts.get(club, 0) + 1
    for p in players:
        pos = p.get("position")
        club = (p.get("clubName") or "").upper()
        can_pos = pos in slots and pos_counts.get(pos, 0) < slots[pos]
        can_club = club_counts.get(club, 0) < max_from_club if club else True
        p["canPick"] = bool(can_pos and can_club)


def _annotate_can_pick_ucl_transfer(players: List[Dict[str, Any]], state: Dict[str, Any], current_user: Optional[str]) -> None:
    """Annotate canPick for transfer in phase - similar to regular but considers transfer limits"""
    if not current_user:
        for p in players:
            p["canPick"] = False
        return
    
    # For transfers, we always allow picking (no draft completion or turn checks)
    roster = (state.get("rosters") or {}).get(current_user, []) or []
    slots = _slots_from_state(state)
    max_from_club = _max_from_club(state)
    
    # Count current positions and clubs (excluding players that were transferred out)
    pos_counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    club_counts: Dict[str, int] = {}
    
    for pl in roster:
        # Skip players that have been transferred out
        if pl.get("status") == "transfer_out":
            continue
            
        pos = pl.get("position")
        if pos in pos_counts:
            pos_counts[pos] += 1
        club = (pl.get("clubName") or "").upper()
        if club:
            club_counts[club] = club_counts.get(club, 0) + 1
    
    for p in players:
        pos = p.get("position")
        club = (p.get("clubName") or "").upper()
        can_pos = pos in slots and pos_counts.get(pos, 0) < slots[pos]
        can_club = club_counts.get(club, 0) < max_from_club if club else True
        p["canPick"] = bool(can_pos and can_club)


def _snake_order(users: List[str], rounds: int) -> List[str]:
    order: List[str] = []
    for r in range(int(rounds)):
        seq = users if r % 2 == 0 else list(reversed(users))
        order.extend(seq)
    return order


def _extract_player_id(entry: Any) -> Optional[int]:
    """Return player identifier from roster/pick entry when available."""
    if not isinstance(entry, dict):
        return None
    for key in ("playerId", "player_id", "playerID", "id", "pid"):
        if key in entry:
            pid = entry.get(key)
            try:
                if pid is not None:
                    return int(pid)
            except (TypeError, ValueError):
                continue
    return None


def _rosters_need_rebuild(state: Dict[str, Any]) -> bool:
    """Detect roster structures that lost player dictionaries."""
    rosters = state.get("rosters")
    if not isinstance(rosters, dict):
        return True
    for roster in rosters.values():
        if roster is None or not isinstance(roster, list):
            return True
        for entry in roster:
            if _extract_player_id(entry) is None:
                return True
    return False


def _rebuild_rosters_from_history(state: Dict[str, Any]) -> bool:
    """Rebuild roster lists using historical picks if structure is broken."""
    picks = state.get("picks") or []
    if not picks:
        return False

    try:
        players_raw = _json_load(UCL_PLAYERS) or []
        lookup_players = {
            int(p.get("playerId")): p
            for p in _players_from_ucl(players_raw)
            if isinstance(p, dict) and p.get("playerId") is not None
        }
    except Exception:
        lookup_players = {}

    old_rosters = state.get("rosters") if isinstance(state.get("rosters"), dict) else {}
    preserved: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for manager, roster in (old_rosters or {}).items():
        if not isinstance(roster, list):
            continue
        meta: Dict[int, Dict[str, Any]] = {}
        for entry in roster:
            pid = _extract_player_id(entry)
            if pid is None or not isinstance(entry, dict):
                continue
            meta[pid] = entry
        if meta:
            preserved[manager] = meta

    rebuilt: Dict[str, List[Dict[str, Any]]] = {user: [] for user in UCL_PARTICIPANTS}
    picks_applied = 0

    for pick in picks:
        if not isinstance(pick, dict) or pick.get("skipped"):
            continue
        manager = pick.get("user") or pick.get("manager") or pick.get("drafter")
        if manager not in rebuilt:
            continue
        pid_val = pick.get("playerId") or pick.get("id") or ((pick.get("player") or {}).get("playerId"))
        try:
            pid = int(pid_val)
        except (TypeError, ValueError):
            continue

        base_entry: Dict[str, Any] = {
            "playerId": pid,
            "fullName": pick.get("player_name"),
            "clubName": pick.get("club"),
            "position": pick.get("pos"),
            "price": pick.get("price"),
        }

        lookup = lookup_players.get(pid)
        if lookup:
            for field in ("fullName", "clubName", "position", "price"):
                if base_entry.get(field) in (None, "") and lookup.get(field) not in (None, ""):
                    base_entry[field] = lookup.get(field)

        preserved_entry = preserved.get(manager, {}).get(pid)
        if preserved_entry:
            merged = dict(preserved_entry)
            for key, value in base_entry.items():
                if value is not None and value != "":
                    merged[key] = value
            entry = merged
        else:
            entry = {k: v for k, v in base_entry.items() if v is not None and v != ""}
            entry["playerId"] = pid

        rebuilt[manager].append(entry)
        picks_applied += 1

    if not picks_applied:
        return False

    for manager, roster in rebuilt.items():
        existing_ids = {p.get("playerId") for p in roster if isinstance(p, dict)}
        old_roster = old_rosters.get(manager) if isinstance(old_rosters, dict) else []
        if isinstance(old_roster, list):
            for entry in old_roster:
                pid = _extract_player_id(entry)
                if pid is None or pid in existing_ids or not isinstance(entry, dict):
                    continue
                roster.append(dict(entry))
                existing_ids.add(pid)

    state["rosters"] = rebuilt
    return True


def _ensure_ucl_state_shape(state: Dict[str, Any]) -> Dict[str, Any]:
    changed = False
    # Ensure rosters for participants only
    rosters = state.get("rosters") or {}
    new_rosters: Dict[str, List[Dict[str, Any]]] = {u: rosters.get(u, []) for u in UCL_PARTICIPANTS}
    if set(rosters.keys()) != set(new_rosters.keys()):
        state["rosters"] = new_rosters
        changed = True
    # Repair roster structure if corrupted (e.g. not lists or missing playerId)
    if _rosters_need_rebuild(state):
        if _rebuild_rosters_from_history(state):
            changed = True
    # Ensure limits
    limits = state.get("limits") or {}
    slots = (limits.get("Slots") if isinstance(limits, dict) else None) or {}
    need_slots = UCL_SLOTS_DEFAULT
    need_max = UCL_MAX_FROM_CLUB_DEFAULT
    if slots != need_slots or limits.get("Max from club") != need_max:
        state["limits"] = {"Slots": need_slots, "Max from club": need_max}
        changed = True
    # Ensure skip bank structure
    if not isinstance(state.get("skip_bank"), dict):
        state["skip_bank"] = {}
        changed = True
    # Ensure draft_order as snake
    existing_order = state.get("draft_order") or []
    desired_order: List[str]
    if not existing_order:
        desired_order = _snake_order(UCL_PARTICIPANTS, UCL_ROUNDS)
    else:
        desired_order = existing_order
        if "Сергей" not in existing_order:
            desired_order = existing_order + ["Сергей"] * UCL_ROUNDS
    if state.get("draft_order") != desired_order:
        state["draft_order"] = desired_order
        changed = True
    # Ensure next_user / next_round coherence
    try:
        idx = int(state.get("current_pick_index", 0))
    except Exception:
        idx = 0
        state["current_pick_index"] = 0
        changed = True
    order = state.get("draft_order") or []
    if idx < 0:
        state["current_pick_index"] = 0
        idx = 0
        changed = True
    elif idx > len(order):
        state["current_pick_index"] = len(order)
        idx = len(order)
        changed = True
    if 0 <= idx < len(order):
        nu = order[idx]
        if state.get("next_user") != nu:
            state["next_user"] = nu
            changed = True
    n_users = len(UCL_PARTICIPANTS) or 1
    nr = (idx // n_users) + 1
    if state.get("next_round") != nr:
        state["next_round"] = nr
        changed = True
    if idx < len(order) and state.get("draft_completed"):
        state["draft_completed"] = False
        changed = True
    if changed:
        _ucl_state_save(state)
    return state

def _ensure_turn_started(state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure current turn fields (turn_user, turn_left) are initialized.
    Uses skip_bank to grant extra picks this turn, then resets that bank for the user.
    """
    try:
        idx = int(state.get("current_pick_index", 0))
    except Exception:
        idx = 0
        state["current_pick_index"] = 0
    order = state.get("draft_order") or []
    if idx >= len(order):
        return state
    tu = state.get("turn_user")
    left = int(state.get("turn_left", 0)) if isinstance(state.get("turn_left"), (int, float)) else 0
    if tu and left > 0:
        return state
    # start new turn for order[idx]
    user = order[idx]
    bank = 0
    try:
        bank = int((state.get("skip_bank") or {}).get(user, 0) or 0)
    except Exception:
        bank = 0
    state.setdefault("skip_bank", {})[user] = 0
    state["turn_user"] = user
    state["turn_left"] = 1 + max(0, bank)
    # keep convenience fields in sync
    state["next_user"] = user
    n_users = len({u for u in (state.get("rosters") or {}).keys()}) or len(UCL_PARTICIPANTS) or 1
    state["next_round"] = (idx // n_users) + 1
    _ucl_state_save(state)
    return state

def _build_status_context_ucl() -> Dict[str, Any]:
    state = _ucl_state_load()
    state = _ensure_ucl_state_shape(state)
    state = _ensure_turn_started(state)
    _flash_stats_refresh_result()
    players_raw = _json_load(UCL_PLAYERS) or []
    pidx = {str(p["playerId"]): p for p in _players_from_ucl(players_raw)}

    limits = state.get("limits") or {"Max from club": 1, "Slots": UCL_SLOTS_DEFAULT}
    slots = _slots_from_state(state)

    picks: List[Dict[str, Any]] = []
    for row in state.get("picks", []):
        pid = str(row.get("playerId") or row.get("id") or "")
        meta = pidx.get(pid, {})
        picks.append(
            {
                "round": row.get("round"),
                "user": row.get("user") or row.get("manager") or row.get("drafter"),
                "player_name": row.get("player_name") or meta.get("fullName"),
                "club": row.get("club") or meta.get("clubName"),
                "pos": row.get("pos") or meta.get("position"),
                "ts": row.get("ts") or row.get("timestamp"),
            }
        )

    squads = state.get("rosters") or state.get("squads") or state.get("teams") or {}
    if (not squads) and picks:
        tmp: Dict[str, List[Dict[str, Any]]] = {}
        for r in picks:
            m = r.get("user") or "Unknown"
            tmp.setdefault(m, [])
            tmp[m].append({"fullName": r.get("player_name"), "position": r.get("pos"), "clubName": r.get("club")})
        squads = tmp

    # Build grouped squads with empty slots for display even if empty
    squads_grouped: Dict[str, Dict[str, List[Dict[str, Any] | None]]] = {}
    for manager in UCL_PARTICIPANTS:
        arr = (squads or {}).get(manager, []) or []
        g = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        for pl in arr:
            pos = (pl.get("position") or "").upper()
            if pos in g:
                g[pos].append({
                    "fullName": pl.get("fullName") or pl.get("player_name"),
                    "position": pos,
                    "clubName": pl.get("clubName") or pl.get("club"),
                })
        for pos in ("GK", "DEF", "MID", "FWD"):
            need = max(0, slots.get(pos, 0) - len(g[pos]))
            g[pos].extend([None] * need)
        squads_grouped[manager] = g

    # Clubs summary: for each club -> total picks and who picked
    clubs_summary: List[Dict[str, Any]] = []
    club_map: Dict[str, Dict[str, Any]] = {}
    for mng, plist in (squads or {}).items():
        for pl in (plist or []):
            club = (pl.get("clubName") or "").strip()
            if not club:
                continue
            item = club_map.setdefault(club, {"club": club, "total": 0, "managers": {}})
            item["total"] += 1
            item["managers"][mng] = item["managers"].get(mng, 0) + 1
    for club, data in club_map.items():
        managers = ", ".join(f"{k} ({v})" for k, v in sorted(data["managers"].items()))
        clubs_summary.append({"club": club, "total": data["total"], "managers": managers})
    clubs_summary.sort(key=lambda x: (-x["total"], x["club"]))

    return {
        "title": "UCL Fantasy Draft — Состояние драфта",
        "draft_url": url_for("ucl.index"),
        "limits": limits,
        "picks": picks,
        "squads": squads,
        "squads_grouped": squads_grouped,
        "clubs_summary": clubs_summary,
        "league_counts": {},  # for template compatibility
        "draft_completed": bool(state.get("draft_completed")),
        "next_user": _who_is_on_clock(state) or state.get("next_user"),
        "next_round": state.get("next_round"),
    }

# ----------------- routes -----------------
@bp.route("/ucl", methods=["GET", "POST"])
def index():
    draft_title = "UCL Fantasy Draft"

    # load data
    raw = _json_load(UCL_PLAYERS) or []
    players = _players_from_ucl(raw)

    # points from previous season
    points_raw = _json_load(UCL_POINTS) or {}
    points_map = _ucl_points_map(points_raw)
    for p in players:
        pid = p.get("playerId")
        p["fp_last"] = points_map.get(pid, 0)

    # points from current season (2025/26) - use curGDPts from UEFA feed
    _ensure_fp_current_from_uefa_feed(players)

    # state
    state = _ucl_state_load()
    state = _ensure_ucl_state_shape(state)
    state = _ensure_turn_started(state)
    _flash_stats_refresh_result()

    # Hide already picked players
    picked_ids = _picked_ids_from_state(state)
    players = [p for p in players if str(p.get("playerId")) not in picked_ids]

    # Handle POST (pick)
    if request.method == "POST":
        current_user = session.get("user_name")
        godmode = bool(session.get("godmode"))
        draft_completed = bool(state.get("draft_completed", False))
        # Action: skip turn
        if (request.form.get("action") == "skip"):
            # Only current on-clock user (or godmode) may skip; god can target via as_user
            target_user = current_user
            if godmode and request.form.get("as_user"):
                target_user = (request.form.get("as_user") or "").strip()
            if godmode or (_who_is_on_clock(state) == target_user):
                # increment bank and record empty pick, advance index and end turn
                sb = state.setdefault("skip_bank", {})
                sb[target_user] = int(sb.get(target_user, 0) or 0) + 1
                state.setdefault("picks", []).append({
                    "round": state.get("next_round"),
                    "user": target_user,
                    "player_name": None,
                    "club": None,
                    "pos": None,
                    "ts": None,
                    "skipped": True,
                })
                # consume this pick slot
                try:
                    idx = int(state.get("current_pick_index", 0)) + 1
                except Exception:
                    idx = 1
                state["current_pick_index"] = idx
                # end current turn immediately
                state["turn_left"] = 0
                state["turn_user"] = None
                # advance next user/round
                order = state.get("draft_order") or []
                if 0 <= idx < len(order):
                    state["next_user"] = order[idx]
                n_users = len({u for u in (state.get("rosters") or {}).keys()}) or len(UCL_PARTICIPANTS) or 1
                state["next_round"] = (idx // n_users) + 1
                if idx >= len(order):
                    state["draft_completed"] = True
                _ucl_state_save(state)
                # After skipping, re-init next turn for header/canPick
                state = _ensure_turn_started(state)
            # Render updated view
            club_filter = ""; pos_filter = ""
            clubs = _uniq_sorted([p.get("clubName") for p in _players_from_ucl(raw)])
            positions = _uniq_sorted([p.get("position") for p in _players_from_ucl(raw)])
            picked_ids = _picked_ids_from_state(state)
            players = [p for p in _players_from_ucl(raw) if str(p.get("playerId")) not in picked_ids]
            filtered = _apply_filters(players, club_filter, pos_filter)
            _annotate_can_pick_ucl(filtered, state, current_user)
            return render_template(
                "index.html",
                draft_title=draft_title,
                players=filtered,
                clubs=clubs,
                positions=positions,
                club_filter=club_filter,
                pos_filter=pos_filter,
                table_league="ucl",
                current_user=current_user,
                next_user=_who_is_on_clock(state) or state.get("next_user"),
                next_round=state.get("next_round"),
                draft_completed=bool(state.get("draft_completed")),
                status_url=url_for("ucl.status"),
                undo_url=url_for("ucl.undo_last_pick"),
                managers=sorted((state.get("rosters") or {}).keys()),
            )
        # Check if transfer window is active and handle transfers
        from .transfer_system import create_transfer_system
        transfer_system = create_transfer_system("ucl")
        transfer_state = transfer_system.load_state()
        transfer_window_active = transfer_system.is_transfer_window_active(transfer_state)
        current_transfer_manager = transfer_system.get_current_transfer_manager(transfer_state)
        current_transfer_phase = transfer_system.get_current_transfer_phase(transfer_state)
        
        if transfer_window_active and current_user == current_transfer_manager:
            # Handle transfer actions
            pid = request.form.get("player_id")
            if pid:
                try:
                    pid = int(pid)
                    if current_transfer_phase == "out":
                        # Transfer player out
                        transfer_system.transfer_player_out(transfer_state, current_user, pid, 1)
                        transfer_system.save_state(transfer_state)
                        flash("Игрок отправлен в transfer out пул! Теперь выберите замену.", "success")
                    elif current_transfer_phase == "in":
                        # Transfer player in
                        transfer_system.transfer_player_in(transfer_state, current_user, pid, 1)
                        transfer_system.save_state(transfer_state)
                        flash("Игрок добавлен в команду! Ход переходит к следующему менеджеру.", "success")
                    
                    # Redirect to avoid resubmission
                    return redirect(url_for('ucl.index'))
                    
                except Exception as e:
                    flash(f"Ошибка трансфера: {str(e)}", "danger")
                    return redirect(url_for('ucl.index'))

        # Initialize variables needed for regular draft picks (if not transfer mode)
        if not transfer_window_active:
            clubs = _uniq_sorted([p.get("clubName") for p in players])
            positions = _uniq_sorted([p.get("position") for p in players])
            club_filter = request.args.get("club", "").strip()
            pos_filter = request.args.get("position", "").strip()
            filtered = _apply_filters(players, club_filter, pos_filter)
            next_user = _who_is_on_clock(state) or state.get("next_user")
            next_round = state.get("next_round")
            draft_completed = bool(state.get("draft_completed"))
            _annotate_can_pick_ucl(filtered, state, session.get("user_name"))
            
            if draft_completed and not godmode:
                return render_template(
                    "index.html",
                    draft_title=draft_title,
                    players=[],
                    clubs=clubs,
                    positions=positions,
                    club_filter=club_filter,
                    pos_filter=pos_filter,
                    table_league="ucl",
                    current_user=current_user,
                    next_user=next_user,
                    next_round=next_round,
                    draft_completed=True,
                    status_url=url_for("ucl.status"),
                    undo_url=url_for("ucl.undo_last_pick"),
                    managers=sorted((state.get("rosters") or {}).keys()),
                )
            pid = request.form.get("player_id")
            pidx = {str(p["playerId"]): p for p in _players_from_ucl(raw)}
            if not pid or pid not in pidx:
                # invalid pick, just re-render GET
                return render_template(
                    "index.html",
                    draft_title=draft_title,
                    players=filtered,
                    clubs=clubs,
                    positions=positions,
                    club_filter=club_filter,
                    pos_filter=pos_filter,
                    table_league="ucl",
                    current_user=current_user,
                    next_user=next_user,
                    next_round=next_round,
                    draft_completed=draft_completed,
                    status_url=url_for("ucl.status"),
                    undo_url=url_for("ucl.undo_last_pick"),
                    managers=sorted((state.get("rosters") or {}).keys()),
                )
            
            # Permissions for regular draft picks
            acting_user = current_user
        if godmode and request.form.get("as_user"):
            acting_user = (request.form.get("as_user") or "").strip()
        on_clock = (_who_is_on_clock(state) == acting_user) or godmode
        if not godmode and (not current_user or not on_clock):
            # forbidden
            return render_template(
                "index.html",
                draft_title=draft_title,
                players=players,
                clubs=_uniq_sorted([p.get("clubName") for p in players]),
                positions=_uniq_sorted([p.get("position") for p in players]),
                club_filter="",
                pos_filter="",
                table_league="ucl",
                current_user=current_user,
                next_user=state.get("next_user") or _who_is_on_clock(state),
                next_round=state.get("next_round"),
                draft_completed=draft_completed,
                status_url=url_for("ucl.status"),
                undo_url=url_for("ucl.undo_last_pick"),
                managers=sorted((state.get("rosters") or {}).keys()),
            )
        # Already picked?
        if pid in picked_ids:
            # simply re-render
            return render_template(
                "index.html",
                draft_title=draft_title,
                players=players,
                clubs=_uniq_sorted([p.get("clubName") for p in players]),
                positions=_uniq_sorted([p.get("position") for p in players]),
                club_filter="",
                pos_filter="",
                table_league="ucl",
                current_user=current_user,
                next_user=state.get("next_user") or _who_is_on_clock(state),
                next_round=state.get("next_round"),
                draft_completed=draft_completed,
                status_url=url_for("ucl.status"),
                undo_url=url_for("ucl.undo_last_pick"),
            )
        # Enforce limits
        meta = pidx[pid]
        roster = (state.get("rosters") or {}).setdefault(acting_user or "", [])
        slots = _slots_from_state(state)
        max_from_club = _max_from_club(state)
        club = (meta.get("clubName") or "").upper()
        pos = meta.get("position")
        # club limit
        if club and sum(1 for x in roster if (x.get("clubName") or "").upper() == club) >= max_from_club and not godmode:
            # refuse silently
            pass
        else:
            # pos limit
            if sum(1 for x in roster if x.get("position") == pos) < slots.get(pos, 0) or godmode:
                new_pl = {
                    "playerId": meta["playerId"],
                    "fullName": meta.get("fullName"),
                    "clubName": meta.get("clubName"),
                    "position": meta.get("position"),
                    "price": meta.get("price"),
                }
                state.setdefault("picks", []).append({
                    "round": state.get("next_round"),
                    "user": acting_user,
                    "player_name": new_pl["fullName"],
                    "club": new_pl["clubName"],
                    "pos": new_pl["position"],
                    "ts": None,
                    "playerId": new_pl["playerId"],
                })
                roster.append(new_pl)
                # advance pick slot
                try:
                    idx = int(state.get("current_pick_index", 0)) + 1
                except Exception:
                    idx = 1
                state["current_pick_index"] = idx
                # consume from current turn's allowance
                try:
                    state["turn_left"] = max(0, int(state.get("turn_left", 0)) - 1)
                except Exception:
                    state["turn_left"] = 0
                # if turn finished, advance to next user; otherwise keep same user on clock
                order = state.get("draft_order") or []
                if int(state.get("turn_left", 0)) <= 0:
                    state["turn_user"] = None
                    if 0 <= idx < len(order):
                        state["next_user"] = order[idx]
                else:
                    # keep same user as on clock
                    state["next_user"] = acting_user
                # recompute round
                n_users = len({u for u in (state.get("rosters") or {}).keys()}) or len(UCL_PARTICIPANTS) or 1
                state["next_round"] = (idx // n_users) + 1
                if idx >= len(order):
                    state["draft_completed"] = True
                _ucl_state_save(state)

        # Redirect to GET to show updated table - redirect to avoid resubmission
        return redirect(url_for('ucl.index'))

    # filters
    club_filter = request.args.get("club", "").strip()
    pos_filter = request.args.get("position", "").strip()

    # options for filters
    clubs = _uniq_sorted([p.get("clubName") for p in players])
    positions = _uniq_sorted([p.get("position") for p in players])

    # apply filters
    filtered = _apply_filters(players, club_filter, pos_filter)

    # state summary for header + canPick
    next_user = _who_is_on_clock(state) or state.get("next_user")
    next_round = state.get("next_round")
    draft_completed = bool(state.get("draft_completed"))
    _annotate_can_pick_ucl(filtered, state, session.get("user_name"))

    # Transfer window info - use same logic as transfer_routes.py
    current_user_name = session.get("user_name")
    transfer_window_active = False
    current_transfer_manager = None
    user_roster = []
    
    try:
        # FORCE the transfer window to be active since we know it works on transfer page
        # The issue is that transfer system reads from different state than UCL main page
        
        # Let's load the transfer system state directly
        from .transfer_system import create_transfer_system
        transfer_system = create_transfer_system("ucl")
        transfer_state = transfer_system.load_state()  # This loads the correct state
        
        # Check legacy window from transfer state (not UCL state)
        legacy_window = transfer_state.get("transfer_window")
        
        if legacy_window and legacy_window.get("active"):
            transfer_window_active = True
            
            # Get manager from legacy window directly
            participant_order = legacy_window.get("participant_order", [])
            current_index = legacy_window.get("current_index", 0)
            participants = [p for p in participant_order if p and p.strip()]
            
            if not participants:
                from .config import UCL_USERS
                participants = UCL_USERS
            
            if current_index < len(participants):
                current_transfer_manager = participants[current_index]
            else:
                current_transfer_manager = None
        else:
            transfer_window_active = False
            current_transfer_manager = None
        
        # Get user's current roster if transfer window is active and it's their turn
        if transfer_window_active and current_user_name == current_transfer_manager:
            rosters = state.get("rosters", {})
            if current_user_name in rosters:
                user_roster = rosters[current_user_name] or []
                # Add fp_current from curGDPts for user roster players
                _ensure_fp_current_from_uefa_feed(user_roster)
                
    except Exception as e:
        print(f"[UCL] Transfer window check error: {e}")
        # Fallback to False values
        transfer_window_active = False
        current_transfer_manager = None
        user_roster = []

    # If transfer window is active and it's current user's turn
    if transfer_window_active and current_user_name == current_transfer_manager:
        # Get current transfer phase
        current_phase = transfer_system.get_current_transfer_phase(transfer_state)
        print(f"[UCL] Transfer mode: phase={current_phase}, manager={current_transfer_manager}")
        
        if current_phase == "out" and user_roster:
            # TRANSFER OUT phase: show user's players for transfer out
            transfer_players = []
            for roster_player in user_roster:
                # Create a copy with required fields
                player_copy = roster_player.copy()
                player_copy["status"] = "owned"  # Mark as owned by current user
                player_copy["canPick"] = True    # Enable clicking (all owned players can be transferred out)
                # Ensure we have the required fields
                if "shortName" not in player_copy:
                    player_copy["shortName"] = player_copy.get("fullName", "")
                transfer_players.append(player_copy)
            
            # Apply filters to user's roster for transfer out
            filtered_transfer_out = _apply_filters(transfer_players, club_filter, pos_filter)
            
            # Replace filtered players with user's roster for transfer mode
            filtered = filtered_transfer_out
            print(f"[UCL] Transfer OUT mode: showing {len(filtered)} players from {current_user_name}'s roster (after filters)")
            
        elif current_phase == "in":
            # TRANSFER IN phase: show available players from transfer out pool
            available_players = transfer_system.get_available_transfer_players(transfer_state)
            transfer_players = []
            
            for available_player in available_players:
                # Create a copy with required fields for table display
                player_copy = available_player.copy()
                player_copy["status"] = "transfer_available"  # Mark as available for transfer in
                # Ensure we have the required fields
                if "shortName" not in player_copy:
                    player_copy["shortName"] = player_copy.get("fullName", "")
                transfer_players.append(player_copy)
            
            # Apply filters to transfer players
            filtered_transfer_players = _apply_filters(transfer_players, club_filter, pos_filter)
            
            # Apply canPick logic with limits checking for transfer in
            _annotate_can_pick_ucl_transfer(filtered_transfer_players, state, current_user_name)
            
            # Replace filtered players with available transfer players
            filtered = filtered_transfer_players
            print(f"[UCL] Transfer IN mode: showing {len(filtered)} available players for transfer in (after filters)")

    return render_template(
        "index.html",
        draft_title=draft_title,
        players=filtered,
        clubs=clubs,
        positions=positions,
        club_filter=club_filter,
        pos_filter=pos_filter,
        table_league="ucl",
        current_user=current_user_name,
        next_user=next_user,
        next_round=next_round,
        draft_completed=draft_completed,
        status_url=url_for("ucl.status"),
        undo_url=url_for("ucl.undo_last_pick"),
        managers=sorted((state.get("rosters") or {}).keys()),
        stats_refresh_running=_stats_refresh_running(),
        # Transfer window info
        transfer_window_active=transfer_window_active,
        current_transfer_manager=current_transfer_manager,
        current_transfer_phase=transfer_system.get_current_transfer_phase(transfer_state) if 'transfer_system' in locals() else None,
        user_roster=user_roster,
    )


@bp.route("/ucl/cache_stats", methods=["POST"])
def cache_stats():
    if not session.get("godmode"):
        abort(403)

    state = _ensure_ucl_state_shape(_ucl_state_load())
    player_ids = _collect_player_ids_for_stats(state)
    if not player_ids:
        flash("Не найдено игроков для обновления статистики", "warning")
        return redirect(url_for("ucl.index"))

    app = current_app._get_current_object()
    if not _start_stats_refresh_job(app, player_ids):
        flash("Обновление статистики уже выполняется", "warning")
    else:
        flash(f"Запущено обновление статистики для {len(player_ids)} игроков", "info")
    return redirect(url_for("ucl.index"))


def _safe_int(val: Any) -> int:
    try:
        if isinstance(val, bool):
            return int(val)
        return int(float(val))
    except Exception:
        return 0


def _normalize_md(val: Any) -> Optional[int]:
    if isinstance(val, (int, float)):
        try:
            return int(val)
        except Exception:
            return None
    if isinstance(val, str):
        digits = "".join(ch for ch in val if ch.isdigit())
        if digits:
            try:
                return int(digits)
            except Exception:
                return None
    return None


def _ucl_default_matchday(state: Dict[str, Any]) -> int:
    md_from_feed = get_current_matchday()
    if md_from_feed:
        return md_from_feed
    try:
        md = int(state.get("next_round") or 1)
    except Exception:
        md = 1
    return max(1, md)


def _ucl_points_for_md(stats: Dict[str, Any], md: int) -> Optional[Dict[str, Any]]:
    if not isinstance(stats, dict):
        return None

    data = stats.get("data") if isinstance(stats.get("data"), dict) else stats
    value = data.get("value") if isinstance(data.get("value"), dict) else data

    def _candidate_lists(container: Dict[str, Any], keys: Tuple[str, ...]) -> List[List[Dict[str, Any]]]:
        result: List[List[Dict[str, Any]]] = []
        for key in keys:
            raw = container.get(key)
            if isinstance(raw, list):
                result.append(raw)
        return result

    point_lists = _candidate_lists(value, ("matchdayPoints", "points"))
    if not point_lists and isinstance(data, dict):
        point_lists = _candidate_lists(data, ("matchdayPoints", "points"))
    if not point_lists and isinstance(stats, dict):
        point_lists = _candidate_lists(stats, ("matchdayPoints", "points"))

    stat_lists = _candidate_lists(value, ("matchdayStats", "stats"))
    if not stat_lists and isinstance(data, dict):
        stat_lists = _candidate_lists(data, ("matchdayStats", "stats"))

    def _find_entry(sources: List[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        for src in sources:
            for entry in src:
                if not isinstance(entry, dict):
                    continue
                md_val = _normalize_md(entry.get("mdId"))
                if md_val is None:
                    continue
                if md_val == int(md):
                    return entry
        return None

    point_entry = _find_entry(point_lists)
    stat_entry = _find_entry(stat_lists)

    stats_count = 0
    for src in stat_lists:
        for entry in src:
            if not isinstance(entry, dict):
                continue
            md_val = _normalize_md(entry.get("mdId"))
            if md_val is None:
                continue
            if md_val == int(md):
                stats_count += 1

    def _normalize_entry(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(entry, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for key, val in entry.items():
            if key == "mdId":
                md_val = _normalize_md(val)
                normalized[key] = md_val if md_val is not None else val
                continue
            if isinstance(val, (int, float)):
                normalized[key] = int(val)
                continue
            if isinstance(val, str):
                digits = "".join(ch for ch in val if (ch.isdigit() or ch in ".-"))
                if digits and any(ch.isdigit() for ch in digits):
                    try:
                        normalized[key] = int(float(digits))
                        continue
                    except Exception:
                        pass
            normalized[key] = val
        return normalized

    normalized_points = _normalize_entry(point_entry)
    normalized_stats = _normalize_entry(stat_entry)

    result: Dict[str, Any] = {
        "points": normalized_points,
        "stats": normalized_stats,
        "tPoints": _safe_int(normalized_points.get("tPoints") or normalized_stats.get("tPoints")),
        "_stats_count": stats_count,
    }

    for key in ("tId", "tName", "teamId", "teamName", "mOM", "isMOM"):
        if key in normalized_stats and normalized_stats[key] is not None:
            result.setdefault(key, normalized_stats[key])
        elif key in normalized_points and normalized_points[key] is not None:
            result.setdefault(key, normalized_points[key])

    return result


@bp.get("/ucl/lineups")
def ucl_lineups():
    state = _ucl_state_load()
    state = _ensure_ucl_state_shape(state)
    _flash_stats_refresh_result()
    md = request.args.get("md", type=int)
    if md is None:
        md = 1
    return render_template(
        "ucl_lineups.html",
        md=md,
        stats_refresh_running=_stats_refresh_running(),
    )


@bp.get("/ucl/lineups/data")
def ucl_lineups_data():
    state = _ucl_state_load()
    state = _ensure_ucl_state_shape(state)
    md = request.args.get("md", type=int)
    if md is None:
        md = 1

    rosters = state.get("rosters") or {}
    managers = [m for m in UCL_PARTICIPANTS if m in rosters]
    if not managers:
        managers = sorted(rosters.keys())
    
    # Get transfer history from both old and new systems
    old_transfer_history = state.get("transfer_history", [])
    new_transfer_history = state.get("transfers", {}).get("history", [])
    
    def get_roster_for_md(manager: str, target_md: int) -> List[Dict]:
        """Get manager's roster as it was for the specific MD"""
        # Start with current roster (after all transfers) and work backwards
        current_roster = list(rosters.get(manager, []))
        
        # Apply old format transfers first (legacy)
        for transfer in old_transfer_history:
            if (transfer.get("manager") == manager and 
                transfer.get("matchday", 999) < target_md):
                
                # Remove transferred out player
                if "player_out" in transfer:
                    out_id = transfer["player_out"].get("playerId")
                    current_roster = [p for p in current_roster if p.get("playerId") != out_id]
                
                # Add transferred in player
                if "player_in" in transfer:
                    in_player = transfer["player_in"]
                    in_player_id = in_player.get("playerId")
                    in_name = in_player.get("fullName", "Unknown")
                    
                    # Check if player is already in roster to avoid duplicates
                    already_in_roster = any(p.get("playerId") == in_player_id for p in current_roster)
                    if not already_in_roster:
                        current_roster.append(in_player)
        
        # Rollback transfers that happened AFTER the target MD
        # For MD1: rollback all transfers (gw >= 1) to get original roster
        # For MD2: rollback transfers with gw >= 3 (keep transfers up to gw 2)
        # Work backwards from current roster to target MD roster
        rollback_transfers = []
        for transfer in new_transfer_history:
            transfer_gw = transfer.get("gw", 999)
            transfer_manager = transfer.get("manager")
            transfer_action = transfer.get("action")
            if transfer_manager == manager:
                if transfer_gw >= target_md:
                    rollback_transfers.append(transfer)
        
        # Sort transfers in REVERSE chronological order for rollback
        rollback_transfers.sort(key=lambda x: x.get("ts", ""), reverse=True)
        
        # Rollback transfers in reverse chronological order
        for transfer in rollback_transfers:
            transfer_gw = transfer.get("gw", 999)
            transfer_manager = transfer.get("manager")
            transfer_action = transfer.get("action")
            # Rollback transfer_in: remove the player that was added
            if transfer_action == "transfer_in" and "in_player" in transfer:
                in_player_id = transfer["in_player"].get("playerId")
                in_name = transfer["in_player"].get("fullName", "Unknown")
                current_roster = [p for p in current_roster if p.get("playerId") != in_player_id]
            
            # Rollback transfer_out: add back the player that was removed
            elif transfer_action == "transfer_out" and "out_player" in transfer:
                out_player = transfer["out_player"]
                out_name = out_player.get("fullName", "Unknown")
                out_player_id = out_player.get("playerId")
                
                # Check if player is already in roster to avoid duplicates
                already_in_roster = any(p.get("playerId") == out_player_id for p in current_roster)
                if not already_in_roster:
                    current_roster.append(out_player)
        
        return current_roster

    def _norm_team_id(raw: Any) -> Optional[str]:
        if raw in (None, "", [], {}):
            return None
        text = str(raw).strip()
        if not text:
            return None
        try:
            num = int(float(text))
            if num <= 0:
                return None
            return str(num)
        except Exception:
            return text or None

    def _first_non_empty(*values: Any) -> Optional[str]:
        for val in values:
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, (int, float)) and val:
                return str(val)
        return None

    def _stat_sections(stats_payload: Any) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        stat_payload: Dict[str, Any] = {}
        points_dict: Dict[str, Any] = {}
        raw_stats: Dict[str, Any] = {}
        data_section: Dict[str, Any] = {}
        value_section: Dict[str, Any] = {}

        if isinstance(stats_payload, dict):
            stat_payload = _ucl_points_for_md(stats_payload, md) or {}
            if isinstance(stat_payload.get("points"), dict):
                points_dict = stat_payload.get("points") or {}
            if isinstance(stat_payload.get("stats"), dict):
                raw_stats = stat_payload.get("stats") or {}

            if isinstance(stats_payload.get("data"), dict):
                data_section = stats_payload.get("data") or {}
                if isinstance(data_section.get("value"), dict):
                    value_section = data_section.get("value") or {}
            if not value_section and isinstance(stats_payload.get("value"), dict):
                value_section = stats_payload.get("value") or {}

        return stat_payload, points_dict, raw_stats, data_section, value_section

    def _resolve_team_id(
        payload: Dict[str, Any],
        stat_payload: Dict[str, Any],
        points_dict: Dict[str, Any],
        raw_stats: Dict[str, Any],
        data_section: Dict[str, Any],
        value_section: Dict[str, Any],
        full_stats: Any,
    ) -> Optional[str]:
        base_stats = full_stats if isinstance(full_stats, dict) else {}
        stats_data = base_stats.get("data") if isinstance(base_stats.get("data"), dict) else {}
        root_value = base_stats.get("value") if isinstance(base_stats.get("value"), dict) else {}

        for candidate in (
            stat_payload.get("teamId"),
            stat_payload.get("tId"),
            raw_stats.get("teamId"),
            raw_stats.get("tId"),
            points_dict.get("teamId"),
            points_dict.get("tId"),
            value_section.get("teamId"),
            value_section.get("teamID"),
            data_section.get("teamId"),
            data_section.get("teamID"),
            root_value.get("teamId"),
            root_value.get("teamID"),
            base_stats.get("teamId"),
            base_stats.get("teamID"),
            stats_data.get("teamId"),
            stats_data.get("teamID"),
            payload.get("teamId"),
            payload.get("clubId"),
        ):
            norm = _norm_team_id(candidate)
            if norm:
                return norm
        return None

    results: Dict[str, Dict[str, Any]] = {}
    for manager in managers:
        # Get roster as it was for this specific MD
        roster = get_roster_for_md(manager, md)
        lineup: List[Dict[str, Any]] = []
        total = 0
        for item in roster:
            payload = item.get("player") if isinstance(item, dict) and item.get("player") else item
            if not isinstance(payload, dict):
                continue
            pid = (
                payload.get("playerId")
                or payload.get("id")
                or payload.get("pid")
            )
            if pid is None:
                continue
            try:
                pid_int = int(pid)
            except Exception:
                continue
            stats = get_player_stats_cached(pid_int)
            stat_payload, points_dict, raw_stats, data_section, value_section = _stat_sections(stats)
            team_id = _resolve_team_id(payload, stat_payload, points_dict, raw_stats, data_section, value_section, stats)
            if not team_id:
                fresh_stats = get_player_stats(pid_int)
                if isinstance(fresh_stats, dict):
                    stats = fresh_stats
                    stat_payload, points_dict, raw_stats, data_section, value_section = _stat_sections(stats)
                    team_id = _resolve_team_id(payload, stat_payload, points_dict, raw_stats, data_section, value_section, stats)
            points = _safe_int(stat_payload.get("tPoints"))
            base_stats = stats if isinstance(stats, dict) else {}
            stats_data = base_stats.get("data") if isinstance(base_stats.get("data"), dict) else {}
            stats_value = base_stats.get("value") if isinstance(base_stats.get("value"), dict) else {}
            club_name = _first_non_empty(
                payload.get("clubName"),
                stat_payload.get("teamName"),
                stat_payload.get("tName"),
                raw_stats.get("teamName"),
                raw_stats.get("tName"),
                points_dict.get("teamName"),
                points_dict.get("tName"),
                value_section.get("teamName"),
                value_section.get("tName"),
                data_section.get("teamName"),
                stats_value.get("teamName"),
                stats_value.get("tName"),
                stats_data.get("teamName"),
                stats_data.get("tName"),
                base_stats.get("teamName"),
                base_stats.get("tName"),
            )
            total += points
            lineup.append(
                {
                    "name": payload.get("fullName") or payload.get("name") or str(pid_int),
                    "pos": payload.get("position"),
                    "club": club_name,
                    "teamId": team_id,
                    "points": points,
                    "stat": stat_payload,
                    "statsCount": stat_payload.get("_stats_count", 0),
                    "playerId": str(pid_int),
                }
            )
        results[manager] = {"players": lineup, "total": total}

    return jsonify({"lineups": results, "managers": managers, "md": md})

@bp.get("/ucl/status")
def status():
    ctx = _build_status_context_ucl()
    return render_template("status.html", **ctx)

@bp.post("/ucl/undo")
def undo_last_pick():
    if not session.get("godmode"):
        from flask import abort
        abort(403)
    state = _ucl_state_load()
    state = _ensure_ucl_state_shape(state)
    picks = state.get("picks") or []
    if not picks:
        # nothing to undo
        _ucl_state_save(state)
        return render_template(
            "index.html",
            draft_title="UCL Fantasy Draft",
            players=[], clubs=[], positions=[],
            club_filter="", pos_filter="",
            table_league="ucl",
            current_user=session.get("user_name"),
            next_user=_who_is_on_clock(state) or state.get("next_user"),
            next_round=state.get("next_round"),
            draft_completed=bool(state.get("draft_completed")),
            status_url=url_for("ucl.status"),
            undo_url=url_for("ucl.undo_last_pick"),
        )
    last = picks.pop()
    user = last.get("user")
    pid = last.get("playerId") or ((last.get("player") or {}).get("playerId"))
    skipped = bool(last.get("skipped")) or (pid is None)
    rosters = state.setdefault("rosters", {})
    if not skipped and user in rosters:
        lst = rosters.get(user) or []
        for i, pl in enumerate(lst):
            if int(pl.get("playerId") or 0) == int(pid):
                lst.pop(i)
                break
    # rewind pick index
    try:
        idx = int(state.get("current_pick_index", 0)) - 1
    except Exception:
        idx = 0
    if idx < 0:
        idx = 0
    state["current_pick_index"] = idx
    # If it was a skipped pick, decrement user's skip bank
    if skipped and user:
        sb = state.setdefault("skip_bank", {})
        try:
            sb[user] = max(0, int(sb.get(user, 0)) - 1)
        except Exception:
            sb[user] = 0
    # reset turn to recompute properly
    state["turn_user"] = None
    state["turn_left"] = 0
    # recompute helpers
    order = state.get("draft_order") or []
    state["next_user"] = order[idx] if 0 <= idx < len(order) else None
    n_users = len({u for u in (state.get("rosters") or {}).keys()}) or len(UCL_PARTICIPANTS) or 1
    state["next_round"] = (idx // n_users) + 1
    state["draft_completed"] = False
    _ucl_state_save(state)
    # Show updated page
    raw = _json_load(UCL_PLAYERS) or []
    players = _players_from_ucl(raw)
    picked_ids = _picked_ids_from_state(state)
    players = [p for p in players if str(p.get("playerId")) not in picked_ids]
    clubs = _uniq_sorted([p.get("clubName") for p in players])
    positions = _uniq_sorted([p.get("position") for p in players])
    _annotate_can_pick_ucl(players, state, session.get("user_name"))
    return render_template(
        "index.html",
        draft_title="UCL Fantasy Draft",
        players=players,
        clubs=clubs,
        positions=positions,
        club_filter="",
        pos_filter="",
        table_league="ucl",
        current_user=session.get("user_name"),
        next_user=_who_is_on_clock(state) or state.get("next_user"),
        next_round=state.get("next_round"),
        draft_completed=bool(state.get("draft_completed")),
        status_url=url_for("ucl.status"),
        undo_url=url_for("ucl.undo_last_pick"),
    )


def _build_ucl_results(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build UCL results data similar to TOP4 results"""
    rosters = state.get("rosters") or {}
    managers = [m for m in UCL_PARTICIPANTS if m in rosters]
    if not managers:
        managers = sorted(rosters.keys())

    # Get list of finished matchdays - only count points from these
    finished_matchdays = set(state.get("finished_matchdays", []))
    
    # Get transfer history from both old and new systems
    old_transfer_history = state.get("transfer_history", [])
    new_transfer_history = state.get("transfers", {}).get("history", [])

    results: Dict[str, Dict[str, Any]] = {}
    
    def safe_int(value) -> int:
        """Convert value to int, return 0 if not possible"""
        try:
            return int(value or 0)
        except (ValueError, TypeError):
            return 0
    
    def get_all_manager_players(manager: str) -> Dict[str, Dict]:
        """Get all players who were ever in manager's roster with their active periods"""
        all_players = {}
        
        # Start with current roster
        current_roster = rosters.get(manager, [])
        # Ensure all roster players have fp_current from curGDPts
        _ensure_fp_current_from_uefa_feed(current_roster)
        for player in current_roster:
            player_id = player.get("playerId")
            if player_id:
                all_players[str(player_id)] = {
                    "player": player,
                    "active_mds": set(range(1, 9)),  # Assume active for all MDs initially
                    "transfer_status": "current"  # current, transfer_in, transfer_out
                }
        
        # Process transfer history to determine actual active periods and transfer status
        all_transfers = []
        
        # Add old format transfers
        for transfer in old_transfer_history:
            if transfer.get("manager") == manager:
                all_transfers.append({
                    "gw": transfer.get("matchday", 1),
                    "action": "combined",
                    "out_player": transfer.get("player_out"),
                    "in_player": transfer.get("player_in"),
                    "ts": transfer.get("ts", "")
                })
        
        # Add new format transfers
        for transfer in new_transfer_history:
            if transfer.get("manager") == manager:
                all_transfers.append({
                    "gw": transfer.get("gw", 1),
                    "action": transfer.get("action"),
                    "out_player": transfer.get("out_player"),
                    "in_player": transfer.get("in_player"),
                    "ts": transfer.get("ts", "")
                })
        
        # Sort transfers by time
        all_transfers.sort(key=lambda x: x.get("ts", ""))
        
        # Apply transfers to determine active periods
        for transfer in all_transfers:
            transfer_gw = transfer.get("gw", 1)
            
            if transfer.get("action") == "combined":
                # Old format: one transfer with both out and in
                out_player = transfer.get("out_player")
                in_player = transfer.get("in_player")
                
                if out_player:
                    out_id = str(out_player.get("playerId"))
                    if out_id in all_players:
                        # Player was active until this transfer (inclusive)
                        all_players[out_id]["active_mds"] = set(range(1, transfer_gw + 1))
                        all_players[out_id]["transfer_status"] = "transfer_out"
                    else:
                        # Add player who was transferred out
                        all_players[out_id] = {
                            "player": out_player,
                            "active_mds": set(range(1, transfer_gw + 1)),
                            "transfer_status": "transfer_out"
                        }
                
                if in_player:
                    in_id = str(in_player.get("playerId"))
                    all_players[in_id] = {
                        "player": in_player,
                        "active_mds": set(range(transfer_gw + 1, 9)),
                        "transfer_status": "transfer_in"
                    }
            
            elif transfer.get("action") == "transfer_out":
                out_player = transfer.get("out_player")
                if out_player:
                    out_id = str(out_player.get("playerId"))
                    if out_id in all_players:
                        # Player was active until this transfer (inclusive)
                        all_players[out_id]["active_mds"] = set(range(1, transfer_gw + 1))
                        all_players[out_id]["transfer_status"] = "transfer_out"
                    else:
                        # Add player who was transferred out
                        all_players[out_id] = {
                            "player": out_player,
                            "active_mds": set(range(1, transfer_gw + 1)),
                            "transfer_status": "transfer_out"
                        }
            
            elif transfer.get("action") == "transfer_in":
                in_player = transfer.get("in_player")
                if in_player:
                    in_id = str(in_player.get("playerId"))
                    all_players[in_id] = {
                        "player": in_player,
                        "active_mds": set(range(transfer_gw + 1, 9)),
                        "transfer_status": "transfer_in"
                    }
        
        return all_players
    
    for manager in managers:
        # Get all players who were ever in this manager's roster
        all_manager_players = get_all_manager_players(manager)
        lineup = []
        total = 0
        
        for player_id, player_data in all_manager_players.items():
            payload = player_data["player"]
            active_mds = player_data["active_mds"]
            transfer_status = player_data["transfer_status"]
            
            if not isinstance(payload, dict):
                continue
                
            pid = payload.get("playerId") or payload.get("id") or payload.get("pid")
            if pid is None:
                continue
                
            try:
                pid_int = int(pid)
            except Exception:
                continue
                
            # Get player stats
            stats = get_player_stats_cached(pid_int)
            if not isinstance(stats, dict):
                stats = {}
            
            # Extract points and team data from stats
            points = 0
            breakdown = []
            team_id = None
            
            # Try to get total points from various possible locations in stats
            if isinstance(stats, dict):
                # First try to get already calculated total points
                data_section = stats.get("data", {})
                if isinstance(data_section, dict):
                    value_section = data_section.get("value", {})
                    if isinstance(value_section, dict):
                        # Try to get team ID
                        team_id = value_section.get("tId") or value_section.get("teamId")
                        
                        # Calculate points only from finished matchdays when player was active
                        matchday_points = value_section.get("matchdayPoints", [])
                        if isinstance(matchday_points, list):
                            for md_stat in matchday_points:
                                if isinstance(md_stat, dict):
                                    md_points = safe_int(md_stat.get("tPoints", 0))
                                    md_num = md_stat.get("mdId")
                                    if md_num and md_points > 0:
                                        # Only include if matchday is finished AND player was active in this MD
                                        if md_num in finished_matchdays and md_num in active_mds:
                                            breakdown.append({
                                                "label": f"MD{md_num}",
                                                "value": md_points
                                            })
                                            points += md_points
                
                # Fallback: try to get team ID if not found
                if not team_id:
                    team_id = stats.get("tId") or stats.get("teamId")
            
            total += points
            lineup.append({
                "name": payload.get("fullName") or payload.get("name") or str(pid_int),
                "pos": payload.get("position"),
                "club": payload.get("clubName"),
                "points": points,
                "breakdown": breakdown,
                "playerId": str(pid_int),
                "teamId": team_id,  # For club logo
                "transfer_status": transfer_status,  # current, transfer_in, transfer_out
                "active_mds": list(active_mds)  # List of MDs when player was active
            })
        
        results[manager] = {"players": lineup, "total": total}
    
    return {"lineups": results, "managers": managers}


@bp.get("/ucl/results")
def ucl_results():
    """UCL results page"""
    return render_template("ucl_results.html")


@bp.get("/ucl/results/data")  
def ucl_results_data():
    """UCL results data API"""
    state = _ucl_state_load()
    state = _ensure_ucl_state_shape(state)
    data = _build_ucl_results(state)
    return jsonify(data)


@bp.route("/ucl/return_transfer_out_player", methods=["POST"])
def return_transfer_out_player():
    """Return the most recent transfer-out player back to their roster (godmode only)."""
    current_user = session.get("user_name")
    if not current_user or not session.get("godmode"):
        abort(403)

    try:
        from .transfer_system import create_transfer_system

        transfer_system = create_transfer_system("ucl")
        state = transfer_system.load_state()
        transfers = state.setdefault("transfers", {})
        history = transfers.setdefault("history", [])
        available_players = transfers.setdefault("available_players", [])

        last_record_index: Optional[int] = None
        last_available_index: Optional[int] = None
        transfer_out_player: Optional[Dict[str, Any]] = None
        manager: Optional[str] = None
        player_id: Optional[str] = None

        # Find the latest transfer_out record that still has a player in the available pool
        for idx in range(len(history) - 1, -1, -1):
            record = history[idx]
            if record.get("action") != "transfer_out":
                continue
            draft_type = str(record.get("draft_type") or "").upper()
            if draft_type and draft_type not in ("UCL", ""):
                continue

            candidate_manager = record.get("manager")
            candidate_player = record.get("out_player") or {}
            candidate_player_id = candidate_player.get("playerId") or candidate_player.get("id")

            if not candidate_manager or candidate_player_id is None:
                continue

            candidate_player_id_str = str(candidate_player_id)

            for avail_idx in range(len(available_players) - 1, -1, -1):
                available_player = available_players[avail_idx]
                available_player_id = available_player.get("playerId") or available_player.get("id")
                if available_player_id is None:
                    continue

                if str(available_player_id) == candidate_player_id_str and \
                        available_player.get("status") == "transfer_out":
                    last_record_index = idx
                    last_available_index = avail_idx
                    transfer_out_player = available_player.copy()
                    manager = candidate_manager
                    player_id = candidate_player_id_str
                    break

            if transfer_out_player is not None:
                break

        if transfer_out_player is None or manager is None or player_id is None or last_record_index is None:
            flash("Нет игроков в пуле transfer out для возврата", "warning")
            return redirect(request.referrer or url_for("home.index"))

        # Remove player from available pool
        if last_available_index is not None:
            available_players.pop(last_available_index)

        # Clean up transfer markers
        transfer_out_player.pop("status", None)
        transfer_out_player.pop("transferred_out_gw", None)
        transfer_out_player.pop("transferred_in_gw", None)

        # Restore player to manager roster if not already there
        rosters = state.setdefault("rosters", {})
        manager_roster = rosters.setdefault(manager, [])
        already_in_roster = any(
            str(p.get("playerId") or p.get("id")) == player_id for p in manager_roster
        )
        if not already_in_roster:
            manager_roster.append(transfer_out_player)

        # Remove the corresponding history record
        history.pop(last_record_index)

        # Reset transfer window phase so manager can choose again
        legacy_window = state.get("transfer_window")
        if isinstance(legacy_window, dict) and legacy_window.get("active"):
            legacy_window["transfer_phase"] = "out"
            legacy_window["current_user"] = manager
            participants = legacy_window.get("participant_order") or []
            if manager in participants:
                legacy_window["current_index"] = participants.index(manager)

        active_window = transfers.get("active_window")
        if isinstance(active_window, dict):
            active_window["transfer_phase"] = "out"
            managers_order = active_window.get("managers_order") or []
            if manager in managers_order:
                active_window["current_manager_index"] = managers_order.index(manager)

        transfer_system.save_state(state)

        flash(
            f"Игрок {transfer_out_player.get('fullName', 'без имени')} возвращён в состав {manager}",
            "success",
        )
        return redirect(request.referrer or url_for("home.index"))

    except Exception as e:
        print(f"Error returning transfer out player: {e}")
        flash(f"Ошибка при возврате игрока: {str(e)}", "danger")
        return redirect(request.referrer or url_for("home.index"))


@bp.route("/ucl/populate_test_rosters", methods=["POST"])
def populate_test_rosters():
    """Populate UCL rosters with test data - GODMODE ONLY"""
    current_user = session.get("user_name")
    if not current_user or not session.get("godmode"):
        abort(403)
    
    try:
        state = _ucl_state_load()
        
        # Load UCL players data
        raw = _json_load(UCL_PLAYERS) or []
        players = _players_from_ucl(raw)
        
        # Get available players (first 200 for testing)
        available_players = players[:200]
        
        # UCL participants
        participants = ["Сергей", "Андрей", "Серёга Б", "Женя", "Ксана", "Саша", "Руслан", "Макс"]
        
        # Group players by position
        players_by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        for player in available_players:
            pos = player.get("position", "MID")
            if pos in players_by_pos:
                players_by_pos[pos].append(player)
        
        print(f"Available players by position: GK={len(players_by_pos['GK'])}, DEF={len(players_by_pos['DEF'])}, MID={len(players_by_pos['MID'])}, FWD={len(players_by_pos['FWD'])}")
        
        # UCL position limits: GK=3, DEF=8, MID=9, FWD=5 (total=25)
        position_limits = {"GK": 3, "DEF": 8, "MID": 9, "FWD": 5}
        
        # Assign players to each participant with proper position distribution
        rosters = state.setdefault("rosters", {})
        
        for i, participant in enumerate(participants):
            participant_roster = []
            
            for pos, limit in position_limits.items():
                pos_players = players_by_pos[pos]
                start_idx = (i * limit) % len(pos_players) if pos_players else 0
                
                for j in range(limit):
                    if pos_players:
                        player_idx = (start_idx + j) % len(pos_players)
                        participant_roster.append(pos_players[player_idx])
            
            rosters[participant] = participant_roster
            print(f"Assigned {len(participant_roster)} players to {participant} (GK: {len([p for p in participant_roster if p.get('position') == 'GK'])}, DEF: {len([p for p in participant_roster if p.get('position') == 'DEF'])}, MID: {len([p for p in participant_roster if p.get('position') == 'MID'])}, FWD: {len([p for p in participant_roster if p.get('position') == 'FWD'])})")
        
        _ucl_state_save(state)
        flash(f"Тестовые составы созданы для {len(participants)} участников", "success")
        
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        print(f"Error populating test rosters: {e}")
        flash(f"Ошибка при создании тестовых составов: {str(e)}", "danger")
        return redirect(request.referrer or url_for("home.index"))


@bp.route("/ucl/open_transfer_window", methods=["POST"])
def open_transfer_window():
    """Open UCL transfer window - godmode only"""
    if not session.get("godmode"):
        abort(403)
    
    try:
        from .transfer_system import init_transfers_for_league
        
        # Calculate current standings to determine transfer order
        state = _ucl_state_load()
        results = _build_ucl_results(state)
        
        # Sort managers by total points (worst first for transfer priority)
        manager_scores = []
        for manager, data in results["lineups"].items():
            total = data.get("total", 0)
            manager_scores.append((manager, total))
            print(f"Manager {manager}: {total} points")
        
        # Sort by total points ascending (worst first)
        manager_scores.sort(key=lambda x: x[1])
        transfer_order = [manager for manager, _ in manager_scores]
        
        print(f"Transfer order (worst to best): {transfer_order}")
        
        # If all managers have same score (0), use predefined order with Сергей first
        if all(score == manager_scores[0][1] for _, score in manager_scores):
            print("All managers have same score, using predefined order")
            transfer_order = ["Сергей", "Андрей", "Серёга Б", "Женя", "Ксана", "Саша", "Руслан", "Макс"]
        
        # Initialize transfer window
        current_matchday = _ucl_default_matchday(state)

        success = init_transfers_for_league(
            draft_type="ucl",
            participants=transfer_order,
            transfers_per_manager=1,  # 1 transfer after MD 1-7
            position_limits={"GK": 3, "DEF": 8, "MID": 9, "FWD": 5},
            max_from_club=1,
            gw=current_matchday,
            total_rounds=1,
        )

        if success:
            flash(
                "Трансферное окно UCL открыто! Очередность: "
                + " → ".join(transfer_order)
                + f" (MD{current_matchday})",
                "success",
            )
        else:
            flash("Ошибка при открытии трансферного окна", "error")
            
    except Exception as e:
        print(f"Error opening UCL transfer window: {e}")
        flash("Ошибка при открытии трансферного окна", "error")
    
    return redirect(url_for("ucl.index"))


@bp.post("/ucl/admin/finish-matchday")
def finish_matchday():
    """Mark a matchday as finished (godmode only)"""
    user_name = session.get("user_name")
    if not session.get("godmode"):
        return jsonify({"error": "Access denied"}), 403
    
    from flask import request
    md = request.form.get("md")
    if not md:
        return jsonify({"error": "Missing matchday number"}), 400
    
    try:
        md_int = int(md)
    except ValueError:
        return jsonify({"error": "Invalid matchday number"}), 400
    
    state = _ucl_state_load()
    finished_matchdays = state.get("finished_matchdays", [])
    
    if md_int not in finished_matchdays:
        finished_matchdays.append(md_int)
        finished_matchdays.sort()
        state["finished_matchdays"] = finished_matchdays
        _ucl_state_save(state)
    
    return jsonify({"success": True, "finished_matchdays": finished_matchdays})


@bp.get("/ucl/admin/matchday-status")
def matchday_status():
    """Get current matchday status"""
    if not session.get("godmode"):
        return jsonify({"error": "Access denied"}), 403
    
    state = _ucl_state_load()
    finished_matchdays = state.get("finished_matchdays", [])
    return jsonify({"finished_matchdays": finished_matchdays})
