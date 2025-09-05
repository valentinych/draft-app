from __future__ import annotations

import json, os, tempfile
from pathlib import Path

import requests
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash

from .top4_services import (
    load_players as load_top4_players,
    players_index as top4_players_index,
    load_state as load_top4_state,
)
from .top4_schedule import build_schedule
from .player_map_store import load_player_map, save_player_map
from .epl_services import _s3_enabled, _s3_bucket, _s3_get_json, _s3_put_json

# Routes related to Top-4 statistics and lineups (formerly "mantra").
bp = Blueprint("top4", __name__, url_prefix="/top4")

API_URL = "https://mantrafootball.org/api/players/{id}/stats"

BASE_DIR = Path(__file__).resolve().parent.parent
ROUND_CACHE_DIR = BASE_DIR / "data" / "cache" / "mantra_rounds"
PLAYER_CACHE_DIR = BASE_DIR / "data" / "cache" / "mantra_players"


def _s3_rounds_prefix() -> str:
    return os.getenv("DRAFT_S3_MANTRA_ROUNDS_PREFIX", "mantra_rounds")


def _s3_key(rnd: int) -> str:
    prefix = _s3_rounds_prefix().strip().strip("/")
    return f"{prefix}/round{int(rnd)}.json"


def _s3_players_prefix() -> str:
    return os.getenv("DRAFT_S3_MANTRA_PLAYERS_PREFIX", "mantra_players")


def _player_key(pid: int) -> str:
    prefix = _s3_players_prefix().strip().strip("/")
    return f"{prefix}/{int(pid)}.json"


def _load_round_cache(rnd: int) -> dict:
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(rnd)
        if bucket and key:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
    p = ROUND_CACHE_DIR / f"round{int(rnd)}.json"
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
        except Exception:
            pass
    return {}


def _save_round_cache(rnd: int, data: dict) -> None:
    payload = {str(k): int(v) for k, v in data.items()}
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(rnd)
        if bucket and key and not _s3_put_json(bucket, key, payload):
            print(f"[MANTRA:S3] save_round_cache fallback rnd={rnd}")
    ROUND_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="round_", suffix=".json", dir=str(ROUND_CACHE_DIR))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ROUND_CACHE_DIR / f"round{int(rnd)}.json")


def _fetch_player(pid: int) -> dict:
    try:
        r = requests.get(API_URL.format(id=pid), timeout=10)
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception:
        return {}


def _save_player_cache(pid: int, data: dict) -> None:
    payload = data or {}
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _player_key(pid)
        if bucket and key and not _s3_put_json(bucket, key, payload):
            print(f"[MANTRA:S3] save_player_cache fallback pid={pid}")
    PLAYER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="player_", suffix=".json", dir=str(PLAYER_CACHE_DIR))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PLAYER_CACHE_DIR / f"{int(pid)}.json")


def _load_player(pid: int) -> dict:
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _player_key(pid)
        if bucket and key:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict):
                return data
    p = PLAYER_CACHE_DIR / f"{int(pid)}.json"
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    data = _fetch_player(pid)
    if data:
        _save_player_cache(pid, data)
    return data


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
        return redirect(url_for("top4.mapping"))

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
    return render_template("top4_mapping.html", mapped=mapped, players=options)


@bp.route("/lineups")
def lineups():
    mapping = load_player_map()
    players = load_top4_players()
    pidx = top4_players_index(players)
    state = load_top4_state()
    rosters = state.get("rosters") or {}

    next_round = int(state.get("next_round") or 1)
    current_round = next_round - 1
    round_no = request.args.get("round", type=int)
    if round_no is None:
        round_no = current_round if current_round > 0 else 1

    cache = _load_round_cache(round_no) if round_no < current_round else {}
    cache_updated = False

    schedule = build_schedule()
    gw_rounds: dict[str, int | None] = {}
    for league, rounds in schedule.items():
        match = next((r for r in rounds if r.get("gw") == round_no), None)
        gw_rounds[league] = match.get("round") if match else None

    results: dict[str, dict] = {}
    for manager, roster in rosters.items():
        lineup = []
        total = 0
        for item in roster or []:
            fid = str(item.get("playerId") or item.get("id"))
            meta = pidx.get(fid, {})
            pos = item.get("position") or meta.get("position")
            name = meta.get("fullName") or meta.get("shortName") or fid
            league = meta.get("league")
            league_round = gw_rounds.get(league)
            mid = mapping.get(fid)
            pts = 0
            if mid and league_round:
                key = str(mid)
                if round_no < current_round:
                    if key in cache:
                        pts = int(cache[key])
                    else:
                        player = _load_player(mid)
                        round_stats = player.get("round_stats", [])
                        stat = next((
                            s for s in round_stats
                            if int(s.get("tournament_round_number", 0)) == league_round
                            and (
                                (s.get("tournament") or {}).get("name") == league
                                or s.get("tournament_name") == league
                                or s.get("league") == league
                            )
                        ), None)
                        pts = _calc_score(stat, pos) if stat else 0
                        cache[key] = int(pts)
                        cache_updated = True
                elif round_no == current_round:
                    player = _load_player(mid)
                    round_stats = player.get("round_stats", [])
                    stat = next((
                        s for s in round_stats
                        if int(s.get("tournament_round_number", 0)) == league_round
                        and (
                            (s.get("tournament") or {}).get("name") == league
                            or s.get("tournament_name") == league
                            or s.get("league") == league
                        )
                    ), None)
                    pts = _calc_score(stat, pos) if stat else 0
                else:
                    pts = 0
            lineup.append({"name": name, "pos": pos, "points": int(pts)})
            total += pts
        lineup.sort(key=lambda r: -r["points"])
        results[manager] = {"players": lineup, "total": int(total)}

    if cache_updated:
        _save_round_cache(round_no, cache)

    managers = sorted(results.keys())
    return render_template(
        "top4_lineups.html",
        lineups=results,
        managers=managers,
        round=round_no,
        gw_rounds=gw_rounds,
    )
