from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List
from flask import Blueprint, render_template, request, session, url_for

bp = Blueprint("ucl", __name__)

# --- файлы данных (подгони пути под свой проект при необходимости) ---
BASE_DIR = Path(__file__).resolve().parent.parent
UCL_STATE = BASE_DIR / "draft_state_ucl.json"
UCL_PLAYERS = BASE_DIR / "players_70_en_3.json"  # список игроков UCL (есть в репо)

# ----------------- helpers -----------------
def _json_load(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None

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
                    "price": p.get("price") if isinstance(p.get("price"), (int, float)) else None,
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
                    "price": p.get("price") if isinstance(p.get("price"), (int, float)) else None,
                }
            )
    return out

def _uniq_sorted(values: List[str]) -> List[str]:
    return sorted({v for v in values if v})

def _apply_filters(players: List[Dict[str, Any]], club: str, pos: str) -> List[Dict[str, Any]]:
    if club:
        players = [p for p in players if (p.get("clubName") or "") == club]
    if pos:
        players = [p for p in players if (p.get("position") or "") == pos]
    return players

def _build_status_context_ucl() -> Dict[str, Any]:
    state = _json_load(UCL_STATE) or {}
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

    return {
        "title": "UCL Fantasy Draft — Состояние драфта",
        "draft_url": url_for("ucl.index"),
        "limits": limits,
        "picks": picks,
        "squads": squads,
        "draft_completed": bool(state.get("draft_completed")),
        "next_user": state.get("next_user"),
        "next_round": state.get("next_round"),
    }

# ----------------- routes -----------------
@bp.get("/ucl")
def index():
    draft_title = "UCL Fantasy Draft"

    # load data
    raw = _json_load(UCL_PLAYERS) or []
    players = _players_from_ucl(raw)

    # filters
    club_filter = request.args.get("club", "").strip()
    pos_filter = request.args.get("position", "").strip()

    # options for filters
    clubs = _uniq_sorted([p.get("clubName") for p in players])
    positions = _uniq_sorted([p.get("position") for p in players])

    # apply filters
    filtered = _apply_filters(players, club_filter, pos_filter)

    # state summary for header
    state = _json_load(UCL_STATE) or {}
    next_user = state.get("next_user")
    next_round = state.get("next_round")
    draft_completed = bool(state.get("draft_completed"))

    return render_template(
        "index.html",
        draft_title=draft_title,
        players=filtered,
        clubs=clubs,
        positions=positions,
        club_filter=club_filter,
        pos_filter=pos_filter,
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
