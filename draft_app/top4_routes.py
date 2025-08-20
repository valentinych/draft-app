from __future__ import annotations
from flask import Blueprint, render_template, request, session, url_for, redirect, abort, flash, jsonify
from datetime import datetime
from typing import Dict

from .top4_services import (
    load_players, players_index,
    load_state, save_state, who_is_on_clock,
    picked_ids_from_state, annotate_can_pick,
    build_status_context,
    wishlist_load, wishlist_save,
)
from .top4_schedule import build_schedule

bp = Blueprint("top4", __name__)

@bp.route("/top4", methods=["GET", "POST"])
def index():
    draft_title = "Top-4 Fantasy Draft"
    players = load_players()
    pidx = players_index(players)
    state = load_state()
    next_user = state.get("next_user") or who_is_on_clock(state)
    draft_completed = bool(state.get("draft_completed", False))
    current_user = session.get("user_name")

    if request.method == "POST":
        if draft_completed:
            flash("Драфт завершён", "warning"); return redirect(url_for("top4.index"))
        if not current_user or current_user != next_user:
            abort(403)
        player_id = request.form.get("player_id")
        if not player_id or player_id not in pidx:
            flash("Некорректный игрок", "danger"); return redirect(url_for("top4.index"))
        picked = picked_ids_from_state(state)
        if str(player_id) in picked:
            flash("Игрок уже выбран", "warning"); return redirect(url_for("top4.index"))
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
                "league": meta.get("league"),
            },
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        state.setdefault("picks", []).append(pick_row)
        state.setdefault("rosters", {}).setdefault(current_user, []).append(pick_row["player"])
        state["current_pick_index"] = int(state.get("current_pick_index", 0)) + 1
        order = state.get("draft_order", [])
        if 0 <= state["current_pick_index"] < len(order):
            state["next_user"] = order[state["current_pick_index"]]
        else:
            state["next_user"] = None
            state["draft_completed"] = True
        save_state(state)
        return redirect(url_for("top4.index"))

    picked_ids = picked_ids_from_state(state)
    players = [p for p in players if str(p["playerId"]) not in picked_ids]

    league_filter = (request.args.get("league") or "").strip()
    club_filter = (request.args.get("club") or "").strip()
    pos_filter = (request.args.get("position") or "").strip()

    leagues = sorted({p.get("league") for p in players if p.get("league")})
    if league_filter:
        players = [p for p in players if p.get("league") == league_filter]
    clubs = sorted({p.get("clubName") for p in players if p.get("clubName")})
    if club_filter:
        players = [p for p in players if p.get("clubName") == club_filter]
    positions = sorted({p.get("position") for p in players if p.get("position")})
    if pos_filter:
        players = [p for p in players if p.get("position") == pos_filter]

    # Sorting
    sort_field = request.args.get("sort") or "price"
    sort_dir = request.args.get("dir") or "desc"
    reverse = sort_dir == "desc"
    if sort_field == "price":
        players.sort(key=lambda p: (p.get("price") is None, p.get("price")), reverse=reverse)
    elif sort_field == "popularity":
        players.sort(key=lambda p: (p.get("popularity") is None, p.get("popularity")), reverse=reverse)

    annotate_can_pick(players, state, current_user)

    return render_template(
        "index.html",
        draft_title=draft_title,
        players=players,
        leagues=leagues,
        clubs=clubs,
        positions=positions,
        league_filter=league_filter,
        club_filter=club_filter,
        pos_filter=pos_filter,
        table_league="top4",
        current_user=current_user,
        next_user=next_user,
        next_round=state.get("next_round"),
        draft_completed=draft_completed,
        status_url=url_for("top4.status"),
    )

@bp.get("/top4/status")
def status():
    ctx = build_status_context()
    ctx["draft_url"] = url_for("top4.index")
    return render_template("status.html", **ctx)


@bp.get("/top4/schedule")
def schedule_view():
    data = build_schedule()
    return render_template("schedule.html", schedule=data)

# ---- Wishlist API ----
@bp.route("/top4/api/wishlist", methods=["GET", "PATCH", "POST"])
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
        to_rm = payload.get("remove") or []
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
