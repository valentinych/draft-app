from __future__ import annotations

import requests
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash

from .epl_services import ensure_fpl_bootstrap_fresh, players_from_fpl, players_index
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
    bootstrap = ensure_fpl_bootstrap_fresh()
    players = players_from_fpl(bootstrap)
    pidx = players_index(players)
    if request.method == "POST":
        fpl_id = request.form.get("fpl_id", type=int)
        mantra_id = request.form.get("mantra_id", type=int)
        if not fpl_id or not mantra_id or str(fpl_id) not in pidx:
            flash("Некорректные ID", "danger")
        else:
            mapping[str(fpl_id)] = int(mantra_id)
            save_player_map(mapping)
            flash("Сохранено", "success")
        return redirect(url_for("mantra.mapping"))
    mapped = []
    for fid, mid in mapping.items():
        meta = pidx.get(str(fid), {})
        mapped.append({
            "fpl_id": fid,
            "name": meta.get("fullName") or meta.get("shortName") or fid,
            "mantra_id": mid,
        })
    mapped.sort(key=lambda x: x["name"])
    return render_template("mantra_mapping.html", mapped=mapped)


@bp.route("/lineups")
def lineups():
    mapping = load_player_map()
    bootstrap = ensure_fpl_bootstrap_fresh()
    players = players_from_fpl(bootstrap)
    pidx = players_index(players)
    round_no = request.args.get("round", type=int) or 1
    results = []
    for fid, mid in mapping.items():
        meta = pidx.get(str(fid), {})
        pos = meta.get("position")
        name = meta.get("fullName") or meta.get("shortName") or fid
        round_stats = _fetch_round_stats(mid)
        stat = next((s for s in round_stats if int(s.get("tournament_round_number", 0)) == round_no), None)
        pts = _calc_score(stat, pos) if stat else 0
        results.append({"name": name, "pos": pos, "points": int(pts)})
    results.sort(key=lambda r: -r["points"])
    return render_template("mantra_lineups.html", players=results, round=round_no)
