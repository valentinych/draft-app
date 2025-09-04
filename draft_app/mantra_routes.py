from __future__ import annotations

import requests
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash

from .top4_services import load_players as load_top4_players, players_index as top4_players_index, load_state as load_top4_state
from .player_map_store import load_player_map, save_player_map

bp = Blueprint("mantra", __name__, url_prefix="/mantra")

API_URL = "https://mantrafootball.org/api/players/{id}/stats"


def _fetch_round_stats(pid: int):
    try:
        r = requests.get(API_URL.format(id=pid), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {})
        return data.get("round_stats", [])
    except Exception:
        return []


def _calc_score(stat: dict, pos: str) -> int:
    mins = int(stat.get("played_minutes", 0))
    score = 0
    if mins >= 60:
        score += 2
    elif mins > 0:
        score += 1
    goals = float(stat.get("goals", 0))
    goal_pts = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}
    score += goal_pts.get(pos, 0) * goals
    assists = float(stat.get("assists", 0))
    score += 3 * assists
    if stat.get("cleansheet") and mins >= 60:
        if pos in ("GK", "DEF"):
            score += 4
        elif pos == "MID":
            score += 1
    if pos == "GK":
        score += 5 * float(stat.get("caught_penalty", 0))
        score += int(int(stat.get("saves", 0)) / 3)
    score -= 2 * float(stat.get("missed_penalty", 0))
    if pos in ("GK", "DEF"):
        score -= int(float(stat.get("missed_goals", 0)) / 2)
    score -= int(stat.get("yellow_card") or 0)
    score -= 3 * int(stat.get("red_card") or 0)
    return int(score)


@bp.route("/mapping", methods=["GET", "POST"])
def mapping():
    if not session.get("godmode"):
        abort(403)
    mapping = load_player_map()
    players = load_top4_players()
    pidx = top4_players_index(players)
    state = load_top4_state()
    rosters = state.get("rosters") or {}
    top4_ids = {str(p.get("playerId") or p.get("id")) for roster in rosters.values() for p in roster or []}

    # Build selector options from drafted players that are not yet mapped
    mapped_ids = set(mapping.keys())
    options = []
    for fid in top4_ids:
        if fid in mapped_ids:
            continue
        meta = pidx.get(fid, {})
        name = meta.get("fullName") or meta.get("shortName") or fid
        options.append({"id": fid, "name": name})
    options.sort(key=lambda x: x["name"])

    if request.method == "POST":
        fpl_id = request.form.get("fpl_id", type=int)
        mantra_id = request.form.get("mantra_id", type=int)
        if not fpl_id or not mantra_id or str(fpl_id) not in pidx or str(fpl_id) not in top4_ids:
            flash("Некорректные ID", "danger")
        else:
            mapping[str(fpl_id)] = int(mantra_id)
            save_player_map(mapping)
            flash("Сохранено", "success")
        return redirect(url_for("mantra.mapping"))

    mapped = []
    for fid, mid in mapping.items():
        if str(fid) not in top4_ids:
            continue
        meta = pidx.get(str(fid), {})
        mapped.append({
            "fpl_id": fid,
            "name": meta.get("fullName") or meta.get("shortName") or fid,
            "mantra_id": mid,
        })
    mapped.sort(key=lambda x: x["name"])
    return render_template("mantra_mapping.html", mapped=mapped, players=options)


@bp.route("/lineups")
def lineups():
    mapping = load_player_map()
    players = load_top4_players()
    pidx = top4_players_index(players)
    state = load_top4_state()
    rosters = state.get("rosters") or {}
    round_no = request.args.get("round", type=int) or 1

    results: dict[str, dict] = {}
    for manager, roster in rosters.items():
        lineup = []
        total = 0
        for item in roster or []:
            fid = str(item.get("playerId") or item.get("id"))
            meta = pidx.get(fid, {})
            pos = item.get("position") or meta.get("position")
            name = meta.get("fullName") or meta.get("shortName") or fid
            mid = mapping.get(fid)
            pts = 0
            if mid:
                round_stats = _fetch_round_stats(mid)
                stat = next((s for s in round_stats if int(s.get("tournament_round_number", 0)) == round_no), None)
                pts = _calc_score(stat, pos) if stat else 0
            lineup.append({"name": name, "pos": pos, "points": int(pts)})
            total += pts
        lineup.sort(key=lambda r: -r["points"])
        results[manager] = {"players": lineup, "total": int(total)}

    managers = sorted(results.keys())
    return render_template("mantra_lineups.html", lineups=results, managers=managers, round=round_no)
