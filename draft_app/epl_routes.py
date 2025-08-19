from __future__ import annotations
from flask import Blueprint, render_template, request, session, url_for, redirect, abort, flash, jsonify
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict

from .epl_services import (
    LAST_SEASON, EPL_FPL,
    ensure_fpl_bootstrap_fresh,
    players_from_fpl, players_index, nameclub_index,
    load_state, save_state, who_is_on_clock,
    picked_fpl_ids_from_state, annotate_can_pick,
    build_status_context,
    wishlist_load, wishlist_save,
    fetch_element_summary, fp_last_from_summary, photo_url_for,
    fixtures_for_gw, points_for_gw,
)
from .lineup_store import load_lineup, save_lineup

bp = Blueprint("epl", __name__)

FORMATIONS = [
    "5-3-2", "5-4-1", "4-3-3", "4-4-2", "4-5-1", "3-4-3", "3-5-2"
]

@bp.route("/epl", methods=["GET", "POST"])
def index():
    draft_title = "EPL Fantasy Draft"

    # Всегда получаем актуальный bootstrap (файл обновится, если старше 1 часа)
    bootstrap = ensure_fpl_bootstrap_fresh()
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

    # Скрываем уже выбранных
    picked_ids = picked_fpl_ids_from_state(state, nidx)
    players = [p for p in players if str(p["playerId"]) not in picked_ids]

    # Фильтры
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

    # Сортировка по цене по умолчанию
    sort_field = request.args.get("sort") or "price"
    sort_dir = request.args.get("dir") or "desc"
    reverse = sort_dir == "desc"
    if sort_field == "price":
        players.sort(key=lambda p: (p.get("price") is None, p.get("price")), reverse=reverse)

    # canPick для фильтра
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


def _formation_counts(fmt: str) -> Dict[str, int]:
    try:
        d, m, f = [int(x) for x in fmt.split("-")]
        return {"GK": 1, "DEF": d, "MID": m, "FWD": f}
    except Exception:
        return {"GK": 1, "DEF": 4, "MID": 4, "FWD": 2}


@bp.route("/epl/squad", methods=["GET", "POST"])
def squad():
    user = session.get("user_name")
    if not user:
        return redirect(url_for("auth.login"))

    gw = request.values.get("gw", type=int) or 1
    bootstrap = ensure_fpl_bootstrap_fresh()
    players = players_from_fpl(bootstrap)
    pidx = players_index(players)
    fixtures_map = fixtures_for_gw(gw, bootstrap)

    state = load_state()
    roster = (state.get("rosters") or {}).get(user, []) or []
    lineup_state = state.setdefault("lineups", {}).setdefault(user, {})
    selected = load_lineup(user, gw) or lineup_state.get(str(gw), {})
    formation = selected.get("formation", "4-4-2")
    lineup_ids = [str(x) for x in (selected.get("players") or [])]
    bench_ids = [str(x) for x in (selected.get("bench") or [])]

    # Add photos
    roster_ext = []
    for pl in roster:
        pid = pl.get("playerId") or pl.get("id")
        meta = pidx.get(str(pid), {})
        team_id = meta.get("teamId")
        stats = meta.get("stats") or {}
        roster_ext.append({
            "playerId": pid,
            "fullName": pl.get("fullName") or meta.get("fullName"),
            "shortName": meta.get("shortName"),
            "position": pl.get("position") or meta.get("position"),
            "clubName": pl.get("clubName") or meta.get("clubName"),
            "photo": photo_url_for(pid),
            "fixture": fixtures_map.get(team_id, ""),
            "status": meta.get("status"),
            "chance": meta.get("chance"),
            "news": meta.get("news"),
            "stats": {
                "minutes": stats.get("minutes"),
                "goals": stats.get("goals"),
                "assists": stats.get("assists"),
                "cs": stats.get("cs"),
                "points": stats.get("points"),
            },
        })

    pos_order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
    roster_ext.sort(key=lambda p: (pos_order.get(p.get("position"), 99), p.get("fullName")))

    # Preselected players with photos
    lineup_ext = []
    for pid in lineup_ids:
        meta = pidx.get(str(pid))
        if meta:
            team_id = meta.get("teamId")
            stats = meta.get("stats") or {}
            lineup_ext.append({
                "playerId": int(pid),
                "fullName": meta.get("fullName"),
                "shortName": meta.get("shortName"),
                "position": meta.get("position"),
                "clubName": meta.get("clubName"),
                "photo": photo_url_for(pid),
                "fixture": fixtures_map.get(team_id, ""),
                "status": meta.get("status"),
                "chance": meta.get("chance"),
                "news": meta.get("news"),
                "stats": {
                    "minutes": stats.get("minutes"),
                    "goals": stats.get("goals"),
                    "assists": stats.get("assists"),
                    "cs": stats.get("cs"),
                    "points": stats.get("points"),
                },
            })

    bench_ext = []
    for pid in bench_ids:
        meta = pidx.get(str(pid))
        if meta:
            team_id = meta.get("teamId")
            stats = meta.get("stats") or {}
            bench_ext.append({
                "playerId": int(pid),
                "fullName": meta.get("fullName"),
                "shortName": meta.get("shortName"),
                "position": meta.get("position"),
                "clubName": meta.get("clubName"),
                "photo": photo_url_for(pid),
                "fixture": fixtures_map.get(team_id, ""),
                "status": meta.get("status"),
                "chance": meta.get("chance"),
                "news": meta.get("news"),
                "stats": {
                    "minutes": stats.get("minutes"),
                    "goals": stats.get("goals"),
                    "assists": stats.get("assists"),
                    "cs": stats.get("cs"),
                    "points": stats.get("points"),
                },
            })

    # Check deadline
    deadline = None
    for ev in (bootstrap.get("events") or []):
        if int(ev.get("id", 0)) == int(gw):
            dl = ev.get("deadline_time")
            if dl:
                try:
                    deadline = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                except Exception:
                    pass
            break
    editable = True
    if deadline:
        editable = datetime.now(timezone.utc) < deadline

    if request.method == "POST" and editable:
        formation = request.form.get("formation", "4-4-2")
        counts = _formation_counts(formation)
        raw_ids = request.form.get("player_ids", "")
        ids = [pid for pid in raw_ids.split(",") if pid]
        raw_bench = request.form.get("bench_ids", "")
        bench = [pid for pid in raw_bench.split(",") if pid]
        # Validate players belong to roster
        roster_ids = {str(p.get("playerId")) for p in roster}
        if not all(pid in roster_ids for pid in ids + bench):
            flash("Некорректный состав", "danger")
        else:
            # Validate positions
            pos_counts = {"GK":0,"DEF":0,"MID":0,"FWD":0}
            for pid in ids:
                pos = pidx.get(pid, {}).get("position")
                if pos in pos_counts:
                    pos_counts[pos]+=1
            valid = (
                len(ids) == 11 and
                pos_counts.get("GK") == 1 and
                pos_counts.get("DEF") == counts["DEF"] and
                pos_counts.get("MID") == counts["MID"] and
                pos_counts.get("FWD") == counts["FWD"] and
                not set(ids) & set(bench)
            )
            if valid:
                payload = {
                    "formation": formation,
                    "players": [int(x) for x in ids],
                    "bench": [int(x) for x in bench],
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                lineup_state[str(gw)] = payload
                save_state(state)
                save_lineup(user, gw, payload)
                flash("Состав сохранён", "success")
                return redirect(url_for("epl.squad", gw=gw))
            else:
                flash("Неверная схема", "warning")

    return render_template(
        "squad.html",
        roster=roster_ext,
        lineup=lineup_ext,
        bench=bench_ext,
        gw=gw,
        formations=FORMATIONS,
        formation=formation,
        editable=editable,
        deadline=deadline,
    )


@bp.get("/epl/lineups")
def lineups():
    gw = request.args.get("gw", type=int) or 1
    state = load_state()
    lineups_state = state.get("lineups") or {}
    rosters = state.get("rosters") or {}
    bootstrap = ensure_fpl_bootstrap_fresh()
    players = players_from_fpl(bootstrap)
    pidx = players_index(players)
    stats_map = points_for_gw(gw, pidx)
    team_codes = {int(t.get("id")): t.get("code") for t in (bootstrap.get("teams") or []) if t.get("id") is not None}
    managers = sorted(rosters.keys())
    table: Dict[str, dict] = {}
    status: Dict[str, bool] = {}
    pos_order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
    for m in managers:
        lineup = (lineups_state.get(m) or {}).get(str(gw))
        starters: list[dict] = []
        bench: list[dict] = []
        ts = None
        if lineup:
            for pid in lineup.get("players") or []:
                meta = pidx.get(str(pid), {})
                name = meta.get("shortName") or meta.get("fullName") or str(pid)
                s = stats_map.get(int(pid), {})
                starters.append({
                    "name": name,
                    "pos": meta.get("position"),
                    "points": s.get("points", 0),
                    "club": team_codes.get(meta.get("teamId")),
                    "minutes": s.get("minutes", 0),
                    "status": s.get("status", "not_started"),
                })
            for pid in lineup.get("bench") or []:
                meta = pidx.get(str(pid), {})
                name = meta.get("shortName") or meta.get("fullName") or str(pid)
                s = stats_map.get(int(pid), {})
                bench.append({
                    "name": name,
                    "pos": meta.get("position"),
                    "points": s.get("points", 0),
                    "club": team_codes.get(meta.get("teamId")),
                    "minutes": s.get("minutes", 0),
                    "status": s.get("status", "not_started"),
                })
            # Add remaining players to bench automatically
            selected = {str(pid) for pid in (lineup.get("players") or []) + (lineup.get("bench") or [])}
            extra = []
            for pl in rosters.get(m, []) or []:
                pid = pl.get("playerId") or pl.get("id")
                if str(pid) in selected:
                    continue
                meta = pidx.get(str(pid), {})
                name = meta.get("shortName") or meta.get("fullName") or pl.get("fullName") or str(pid)
                s = stats_map.get(int(pid), {})
                extra.append({
                    "name": name,
                    "pos": pl.get("position") or meta.get("position"),
                    "points": s.get("points", 0),
                    "club": team_codes.get(meta.get("teamId")),
                    "minutes": s.get("minutes", 0),
                    "status": s.get("status", "not_started"),
                })
            extra.sort(key=lambda p: pos_order.get(p.get("pos"), 99))
            bench.extend(extra)
            # Apply automatic substitutions / penalties: if starter didn't play and match finished,
            # try to replace with first bench player of same position who played minutes > 0.
            # If no such bench player exists, assign -2 points to the starter.
            for s in starters:
                if s["status"] == "finished" and s.get("minutes", 0) == 0:
                    s["subbed_out"] = True
                    replaced = False
                    for b in bench:
                        if (
                            b.get("pos") == s.get("pos")
                            and b.get("minutes", 0) > 0
                            and not b.get("subbed_in")
                        ):
                            b["subbed_in"] = True
                            replaced = True
                            break
                    if not replaced:
                        s["penalized"] = True
                        s["points"] = -2
            # Lineup timestamp
            ts_raw = lineup.get("ts")
            if ts_raw:
                try:
                    ts = datetime.fromisoformat(ts_raw).astimezone(ZoneInfo("Europe/Warsaw"))
                except Exception:
                    ts = None
            status[m] = True
        else:
            for pl in rosters.get(m, []) or []:
                pid = pl.get("playerId") or pl.get("id")
                meta = pidx.get(str(pid), {})
                name = meta.get("shortName") or meta.get("fullName") or pl.get("fullName") or str(pid)
                s = stats_map.get(int(pid), {})
                starters.append({
                    "name": name,
                    "pos": pl.get("position") or meta.get("position"),
                    "points": s.get("points", 0),
                    "club": team_codes.get(meta.get("teamId")),
                    "minutes": s.get("minutes", 0),
                    "status": s.get("status", "not_started"),
                })
            starters.sort(key=lambda p: pos_order.get(p.get("pos"), 99))
            status[m] = False
        players_cnt = len(lineup.get("players") or []) if lineup else 0
        if lineup and players_cnt == 11:
            total_pts = 0
            for s in starters:
                if s.get("subbed_out") and not s.get("penalized"):
                    # points counted from bench player already
                    continue
                total_pts += s.get("points", 0)
            for b in bench:
                if b.get("subbed_in"):
                    total_pts += b.get("points", 0)
        else:
            total_pts = None
        table[m] = {
            "starters": starters,
            "bench": bench,
            "has_lineup": status[m],
            "ts": ts,
            "total": total_pts,
        }

    deadline = None
    for ev in (bootstrap.get("events") or []):
        if int(ev.get("id", 0)) == int(gw):
            dl = ev.get("deadline_time")
            if dl:
                try:
                    deadline = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                except Exception:
                    pass
            break

    deadline_warsaw = None
    deadline_minsk = None
    if deadline:
        deadline_warsaw = deadline.astimezone(ZoneInfo("Europe/Warsaw"))
        deadline_minsk = deadline.astimezone(ZoneInfo("Europe/Minsk"))

    return render_template(
        "lineups.html",
        gw=gw,
        managers=managers,
        lineups=table,
        status=status,
        deadline_warsaw=deadline_warsaw,
        deadline_minsk=deadline_minsk,
    )

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
