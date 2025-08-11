from __future__ import annotations
from flask import Blueprint, render_template, request, session, url_for, redirect, abort, flash, jsonify
from datetime import datetime
from typing import Any, Dict, List

from .epl_services import (
    BASE_DIR, EPL_FPL, LAST_SEASON,
    players_from_fpl, players_index, nameclub_index,
    load_state, save_state, who_is_on_clock, slots_from_state,
    picked_fpl_ids_from_state, annotate_can_pick,
    build_status_context,
    wishlist_load, wishlist_save,
    fetch_element_summary, fp_last_from_summary, photo_url_for,
)

import json

bp = Blueprint("epl", __name__)

@bp.route("/epl", methods=["GET", "POST"])
def index():
    draft_title = "EPL Fantasy Draft"
    bootstrap = _json_load(EPL_FPL) or {}
    players = players_from_fpl(bootstrap)
    pidx = players_index(players)
    nidx = nameclub_index(players)

    state = load_state()
    next_user = state.get("next_user") or who_is_on_clock(state)
    next_round = state.get("next_round")
    draft_completed = bool(state.get("draft_completed", False))
    current_user = session.get("user_name")
    godmode = bool(session.get("godmode"))

    if request.method == "POST":
        if draft_completed:
            flash("Драфт завершён", "warning"); return redirect(url_for("epl.index"))
        player_id = request.form.get("player_id")
        if not player_id or player_id not in pidx:
            flash("Некорректный игрок", "danger"); return redirect(url_for("epl.index"))
        if not godmode and (not current_user or current_user != next_user):
            abort(403)
        picked_ids = picked_fpl_ids_from_state(state, nidx)
        if str(player_id) in picked_ids:
            flash("Игрок уже выбран", "warning"); return redirect(url_for("epl.index"))
        if not state.get("draft_started_at"):
            state["draft_started_at"] = datetime.now().isoformat(timespec="seconds")
        meta = pidx[str(player_id)]
        pick_row = {
            "user": current_user,
            "player": {
                "playerId": meta["playerId"],
                "fullName": meta.get("fullName"),
                "clubName": meta.get("clubName"),
                "position": meta.get("position"),
                "price": meta.get("price"),
            },
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        state.setdefault("picks", []).append(pick_row)
        state.setdefault("rosters", {}).setdefault(current_user, []).append(pick_row["player"])
        try:
            state["current_pick_index"] = int(state.get("current_pick_index", 0)) + 1
            order = state.get("draft_order", [])
            if 0 <= state["current_pick_index"] < len(order):
                state["next_user"] = order[state["current_pick_index"]]
        except Exception:
            pass
        save_state(state)
        return redirect(url_for("epl.index"))

    picked_ids = picked_fpl_ids_from_state(state, nidx)
    players = [p for p in players if str(p["playerId"]) not in picked_ids]

    club_filter = (request.args.get("club") or "").strip()
    pos_filter  = (request.args.get("position") or "").strip()
    clubs = sorted({p.get("clubName") for p in players if p.get("clubName")})
    positions = sorted({p.get("position") for p in players if p.get("position")})

    teams = (bootstrap.get("teams") or [])
    abbr2name = {str(t.get("short_name")).upper(): t.get("name") for t in teams if t.get("short_name") and t.get("name")}
    name2abbr = {v.upper(): k for k, v in abbr2name.items()}
    if club_filter and club_filter not in clubs:
        club_filter = name2abbr.get(club_filter.upper(), "")
    if club_filter:
        club_key = club_filter.upper()
        players = [p for p in players if (p.get("clubName") or "").upper() == club_key]
    if pos_filter:
        players = [p for p in players if (p.get("position") or "") == pos_filter]

    sort_field = request.args.get("sort") or "price"
    sort_dir = request.args.get("dir") or "desc"
    reverse = sort_dir == "desc"
    if sort_field == "price":
        players.sort(key=lambda p: (p.get("price") is None, p.get("price")), reverse=reverse)

    annotate_can_pick(players, state, current_user)

    return render_template(
        "index.html",
        draft_title=draft_title,
        players=players,
        clubs=clubs,
        positions=positions,
        club_filter=club_filter,
        pos_filter=pos_filter,
        current_user=current_user,
        next_user=next_user,
        next_round=next_round,
        draft_completed=draft_completed,
        status_url=url_for("epl.status"),
    )

@bp.get("/epl/status")
def status():
    ctx = build_status_context()
    return render_template("status.html", **ctx)

@bp.post("/epl/undo")
def undo_last_pick():
    if not session.get("godmode"):
        abort(403)
    state = load_state()
    picks = state.get("picks") or []
    if not picks:
        flash("Нет пиков для отмены", "warning")
        return redirect(url_for("epl.index"))
    last = picks.pop()
    user = last.get("user")
    pl = (last.get("player") or {})
    pid = pl.get("playerId")
    roster = (state.get("rosters") or {}).get(user)
    if isinstance(roster, list) and pid is not None:
        for i, it in enumerate(roster):
            if (isinstance(it, dict) and (it.get("playerId") == pid or it.get("id") == pid)):
                roster.pop(i); break
    try:
        idx = int(state.get("current_pick_index", 0)) - 1
        if idx < 0: idx = 0
        state["current_pick_index"] = idx
        order = state.get("draft_order", [])
        state["next_user"] = order[idx] if 0 <= idx < len(order) else None
    except Exception:
        pass
    state["draft_completed"] = False
    save_state(state)
    flash("Последний пик отменён", "success")
    return redirect(url_for("epl.index"))

# ---- Wishlist API ----
@bp.route("/epl/api/wishlist", methods=["GET", "PATCH", "POST"])
def wishlist_api():
    user = session.get("user_name")
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if request.method == "GET":
        ids = wishlist_load(user)
        return jsonify({"manager": user, "ids": ids})
    if request.method == "PATCH":
        payload = request.get_json(silent=True) or {}
        to_add = payload.get("add") or []
        to_rm  = payload.get("remove") or []
        try:
            cur = set(wishlist_load(user))
            cur.update(int(x) for x in to_add)
            cur.difference_update(int(x) for x in to_rm)
            ids = sorted(cur)
            wishlist_save(user, ids)
            return jsonify({"ok": True, "ids": ids})
        except Exception as e:
            return jsonify({"error": "bad payload", "details": str(e)}), 400
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        ids = payload.get("ids")
        if not isinstance(ids, list):
            return jsonify({"error": "ids must be list"}), 400
        try:
            wishlist_save(user, [int(x) for x in ids])
            return jsonify({"ok": True, "ids": wishlist_load(user)})
        except Exception as e:
            return jsonify({"error": "cannot save", "details": str(e)}), 400
    return jsonify({"error": "method not allowed"}), 405

# ---- Player stats + FP API ----
@bp.get("/epl/api/player/<int:pid>/stats")
def player_stats(pid: int):
    summary = fetch_element_summary(pid)
    history = summary.get("history_past") or []
    hist_norm = []
    for r in history:
        hist_norm.append({
            "season": r.get("season_name") or r.get("season") or "",
            "minutes": r.get("minutes"),
            "goals": r.get("goals_scored") or r.get("goals"),
            "assists": r.get("assists"),
            "cs": r.get("clean_sheets") or r.get("cleanSheets"),
            "total_points": r.get("total_points") or r.get("points"),
        })
    return jsonify({
        "playerId": pid,
        "history": hist_norm,
        "fp_last": fp_last_from_summary(summary) or 0,
        "season_label": LAST_SEASON,
        "photo_url": photo_url_for(pid),
        "cached": True,
    })

@bp.get("/epl/api/fp_last")
def fp_last_batch():
    ids_q = (request.args.get("ids") or "").strip()
    if not ids_q:
        return jsonify({"fp": {}, "season": LAST_SEASON})
    try:
        ids = [int(x) for x in ids_q.split(",") if x.strip().isdigit()]
    except Exception:
        return jsonify({"fp": {}, "season": LAST_SEASON})
    fp = {}
    for pid in ids:
        fp[str(pid)] = fp_last_from_summary(fetch_element_summary(pid)) or 0
    return jsonify({"fp": fp, "season": LAST_SEASON})

# ---- tiny helper ----
def _json_load(path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
