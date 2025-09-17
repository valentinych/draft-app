from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from flask import Blueprint, render_template, request, session, url_for, jsonify
import os
try:
    import boto3
except Exception:
    boto3 = None

from .ucl_stats_store import get_player_stats, get_current_matchday

bp = Blueprint("ucl", __name__)

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
                        # Assume can pick by default; server can refine later
                        "canPick": True,
                    }
                )
    return out


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

def _snake_order(users: List[str], rounds: int) -> List[str]:
    order: List[str] = []
    for r in range(int(rounds)):
        seq = users if r % 2 == 0 else list(reversed(users))
        order.extend(seq)
    return order

def _ensure_ucl_state_shape(state: Dict[str, Any]) -> Dict[str, Any]:
    changed = False
    # Ensure rosters for participants only
    rosters = state.get("rosters") or {}
    new_rosters: Dict[str, List[Dict[str, Any]]] = {u: rosters.get(u, []) for u in UCL_PARTICIPANTS}
    if set(rosters.keys()) != set(new_rosters.keys()):
        state["rosters"] = new_rosters
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

    # state
    state = _ucl_state_load()
    state = _ensure_ucl_state_shape(state)
    state = _ensure_turn_started(state)

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
        if draft_completed and not godmode:
            return render_template(
                "index.html",
                draft_title=draft_title,
                players=[],
                clubs=[],
                positions=[],
                club_filter="",
                pos_filter="",
                table_league="ucl",
                current_user=current_user,
                next_user=state.get("next_user"),
                next_round=state.get("next_round"),
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
        # Permissions
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

        # Redirect to GET to show updated table
        return render_template(
            "index.html",
            draft_title=draft_title,
            players=[p for p in _players_from_ucl(raw) if str(p.get("playerId")) not in _picked_ids_from_state(state)],
            clubs=_uniq_sorted([p.get("clubName") for p in _players_from_ucl(raw)]),
            positions=_uniq_sorted([p.get("position") for p in _players_from_ucl(raw)]),
            club_filter="",
            pos_filter="",
            table_league="ucl",
            current_user=session.get("user_name"),
            next_user=_who_is_on_clock(state) or state.get("next_user"),
            next_round=state.get("next_round"),
            draft_completed=bool(state.get("draft_completed")),
            status_url=url_for("ucl.status"),
            undo_url=url_for("ucl.undo_last_pick"),
            managers=sorted((state.get("rosters") or {}).keys()),
        )

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

    return render_template(
        "index.html",
        draft_title=draft_title,
        players=filtered,
        clubs=clubs,
        positions=positions,
        club_filter=club_filter,
        pos_filter=pos_filter,
        table_league="ucl",
        current_user=session.get("user_name"),
        next_user=next_user,
        next_round=next_round,
        draft_completed=draft_completed,
        status_url=url_for("ucl.status"),
        undo_url=url_for("ucl.undo_last_pick"),
        managers=sorted((state.get("rosters") or {}).keys()),
    )


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

    if point_entry and stat_entry and point_entry is not stat_entry:
        merged = {**stat_entry, **point_entry}
    else:
        merged = point_entry or stat_entry

    if not isinstance(merged, dict):
        return None

    normalized: Dict[str, Any] = {}
    for key, val in merged.items():
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

    if "tPoints" not in normalized and isinstance(point_entry, dict):
        normalized["tPoints"] = _safe_int(point_entry.get("tPoints"))

    normalized.setdefault("tPoints", 0)
    return normalized


@bp.get("/ucl/lineups")
def ucl_lineups():
    state = _ucl_state_load()
    md = request.args.get("md", type=int)
    if not md:
        md = _ucl_default_matchday(state)
    return render_template("ucl_lineups.html", md=md)


@bp.get("/ucl/lineups/data")
def ucl_lineups_data():
    state = _ucl_state_load()
    state = _ensure_ucl_state_shape(state)
    md = request.args.get("md", type=int)
    if not md:
        md = _ucl_default_matchday(state)

    rosters = state.get("rosters") or {}
    managers = [m for m in UCL_PARTICIPANTS if m in rosters]
    if not managers:
        managers = sorted(rosters.keys())

    results: Dict[str, Dict[str, Any]] = {}
    for manager in managers:
        roster = rosters.get(manager) or []
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
            stats = get_player_stats(pid_int)
            points_entry = _ucl_points_for_md(stats, md)
            stat_payload: Dict[str, Any] = points_entry or {}
            points = _safe_int(stat_payload.get("tPoints")) if stat_payload else 0
            total += points
            lineup.append(
                {
                    "name": payload.get("fullName") or payload.get("name") or str(pid_int),
                    "pos": payload.get("position"),
                    "club": payload.get("clubName"),
                    "points": points,
                    "stat": stat_payload,
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
