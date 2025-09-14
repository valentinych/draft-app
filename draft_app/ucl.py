from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from flask import Blueprint, render_template, request, session, url_for

bp = Blueprint("ucl", __name__)

# --- файлы данных (подгони пути под свой проект при необходимости) ---
BASE_DIR = Path(__file__).resolve().parent.parent
UCL_STATE = BASE_DIR / "draft_state_ucl.json"
UCL_PLAYERS = BASE_DIR / "players_80_en_1.json"  # актуальный список игроков
UCL_POINTS = BASE_DIR / "players_70_en_3.json"   # очки прошлого сезона

# --- параметры UCL драфта ---
UCL_PARTICIPANTS = ["Саша", "Руслан", "Женя", "Андрей", "Ксана", "Макс"]
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
    # Ensure draft_order as snake
    desired_order = _snake_order(UCL_PARTICIPANTS, UCL_ROUNDS)
    if state.get("draft_order") != desired_order:
        state["draft_order"] = desired_order
        # reset derived fields if index out of bounds
        idx = int(state.get("current_pick_index", 0))
        if idx < 0 or idx >= len(desired_order):
            state["current_pick_index"] = 0
        changed = True
    # Ensure next_user / next_round coherence
    try:
        idx = int(state.get("current_pick_index", 0))
    except Exception:
        idx = 0
        state["current_pick_index"] = 0
        changed = True
    order = state.get("draft_order") or []
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
    if changed:
        _json_dump_atomic(UCL_STATE, state)
    return state

def _build_status_context_ucl() -> Dict[str, Any]:
    state = _json_load(UCL_STATE) or {}
    state = _ensure_ucl_state_shape(state)
    players_raw = _json_load(UCL_PLAYERS) or []
    pidx = {str(p["playerId"]): p for p in _players_from_ucl(players_raw)}

    limits = state.get("limits") or {"Max from club": 3, "Min GK": 1, "Min DEF": 3, "Min MID": 3, "Min FWD": 1}

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

    squads = state.get("squads") or state.get("teams") or {}
    if not squads and picks:
        tmp: Dict[str, List[Dict[str, Any]]] = {}
        for r in picks:
            m = r.get("user") or "Unknown"
            tmp.setdefault(m, [])
            tmp[m].append({"fullName": r.get("player_name"), "position": r.get("pos"), "clubName": r.get("club")})
        squads = tmp

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
        "clubs_summary": clubs_summary,
        "draft_completed": bool(state.get("draft_completed")),
        "next_user": state.get("next_user"),
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
    state = _json_load(UCL_STATE) or {}
    state = _ensure_ucl_state_shape(state)

    # Hide already picked players
    picked_ids = _picked_ids_from_state(state)
    players = [p for p in players if str(p.get("playerId")) not in picked_ids]

    # Handle POST (pick)
    if request.method == "POST":
        current_user = session.get("user_name")
        godmode = bool(session.get("godmode"))
        draft_completed = bool(state.get("draft_completed", False))
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
            )
        # Permissions
        on_clock = (_who_is_on_clock(state) == current_user)
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
            )
        # Enforce limits
        meta = pidx[pid]
        roster = (state.get("rosters") or {}).setdefault(current_user or "", [])
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
                    "user": current_user,
                    "player_name": new_pl["fullName"],
                    "club": new_pl["clubName"],
                    "pos": new_pl["position"],
                    "ts": None,
                    "playerId": new_pl["playerId"],
                })
                roster.append(new_pl)
                # advance turn
                try:
                    idx = int(state.get("current_pick_index", 0)) + 1
                except Exception:
                    idx = 1
                state["current_pick_index"] = idx
                order = state.get("draft_order") or []
                if 0 <= idx < len(order):
                    state["next_user"] = order[idx]
                # recompute round
                n_users = len({u for u in (state.get("rosters") or {}).keys()}) or 1
                state["next_round"] = (idx // n_users) + 1
                if idx >= len(order):
                    state["draft_completed"] = True
                _json_dump_atomic(UCL_STATE, state)

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
            next_user=state.get("next_user") or _who_is_on_clock(state),
            next_round=state.get("next_round"),
            draft_completed=bool(state.get("draft_completed")),
            status_url=url_for("ucl.status"),
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
    next_user = state.get("next_user") or _who_is_on_clock(state)
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
    )

@bp.get("/ucl/status")
def status():
    ctx = _build_status_context_ucl()
    return render_template("status.html", **ctx)
