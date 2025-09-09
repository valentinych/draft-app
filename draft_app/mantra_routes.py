from __future__ import annotations

import json, os, tempfile
from pathlib import Path
from threading import Thread, Lock

import requests
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    abort,
    flash,
    jsonify,
)

from .top4_services import (
    load_players as load_top4_players,
    players_index as top4_players_index,
    load_state as load_top4_state,
    save_state as save_top4_state,
    _s3_enabled,
    _s3_bucket,
    _s3_get_json,
    _s3_put_json,
)
from .top4_schedule import build_schedule
from .player_map_store import load_player_map, save_player_map
from .top4_score_store import load_top4_score, save_top4_score

# Routes related to Top-4 statistics and lineups (formerly "mantra").
bp = Blueprint("top4", __name__, url_prefix="/top4")

API_URL = "https://mantrafootball.org/api/players/{id}/stats"

BASE_DIR = Path(__file__).resolve().parent.parent
ROUND_CACHE_DIR = BASE_DIR / "data" / "cache" / "mantra_rounds"
LINEUPS_DIR = BASE_DIR / "data" / "cache" / "top4_lineups"

POS_ORDER = {
    "GKP": 0,
    "GK": 0,
    "G": 0,
    "DEF": 1,
    "D": 1,
    "MID": 2,
    "M": 2,
    "FWD": 3,
    "F": 3,
}

BUILDING_ROUNDS: set[int] = set()
BUILDING_LOCK = Lock()


def _s3_rounds_prefix() -> str:
    return os.getenv("DRAFT_S3_MANTRA_ROUNDS_PREFIX", "mantra_rounds")


def _s3_key(rnd: int) -> str:
    prefix = _s3_rounds_prefix().strip().strip("/")
    return f"{prefix}/round{int(rnd)}.json"


def _s3_lineups_prefix() -> str:
    return os.getenv("DRAFT_S3_TOP4_LINEUPS_PREFIX", "top4_lineups")


def _s3_lineups_key(rnd: int) -> str:
    prefix = _s3_lineups_prefix().strip().strip("/")
    return f"{prefix}/round{int(rnd)}.json"


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


def _save_lineups_json(rnd: int, data: dict) -> None:
    """Persist full lineup data for debugging (S3 + local)."""
    payload = data or {}
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_lineups_key(rnd)
        if bucket and key and not _s3_put_json(bucket, key, payload):
            print(f"[MANTRA:S3] save_lineups_json fallback rnd={rnd}")
    LINEUPS_DIR.mkdir(parents=True, exist_ok=True)
    p = LINEUPS_DIR / f"round{int(rnd)}.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_lineups_json(rnd: int) -> dict | None:
    def _sort_payload(data: dict) -> dict:
        lineups = data.get("lineups")
        if isinstance(lineups, dict):
            for lineup in lineups.values():
                players = lineup.get("players")
                if isinstance(players, list):
                    players.sort(
                        key=lambda r: POS_ORDER.get(
                            (r.get("pos") or "").strip().upper(), 99
                        )
                    )
        return data

    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_lineups_key(rnd)
        if bucket and key:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict) and data:
                return _sort_payload(data)
    p = LINEUPS_DIR / f"round{int(rnd)}.json"
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return _sort_payload(data)
        except Exception:
            pass
    return None


def _fetch_player(pid: int) -> dict:
    try:
        print(f"[MANTRA] request pid={pid}")
        stats_resp = requests.get(API_URL.format(id=pid), timeout=10)
        stats_resp.raise_for_status()
        stats_data = stats_resp.json()

        LINEUPS_DIR.mkdir(parents=True, exist_ok=True)
        (LINEUPS_DIR / f"stats{pid}.json").write_text(
            json.dumps(stats_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        info_resp = requests.get(f"https://mantrafootball.org/api/players/{pid}", timeout=10)
        info_resp.raise_for_status()
        info_data = info_resp.json()
        (LINEUPS_DIR / f"{pid}.json").write_text(
            json.dumps(info_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return stats_data.get("data", {})
    except Exception as exc:
        print(f"[MANTRA] fetch failed pid={pid}: {exc}")
        return {}

def _load_player(pid: int, debug: list[str] | None = None) -> dict:
    data = load_top4_score(pid)
    if data:
        if debug is not None:
            debug.append(f"cached pid {pid}")
        return data
    data = _fetch_player(pid)
    if debug is not None:
        state = "ok" if data else "empty"
        debug.append(f"fetched pid {pid}: {state}")
    # Persist even empty payloads so repeated failures are visible on disk
    save_top4_score(pid, data)
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


def _resolve_round() -> tuple[int, int, dict]:
    state = load_top4_state()
    schedule = build_schedule()
    current_round = next(
        (rd.get("gw") for rounds in schedule.values() for rd in rounds if rd.get("current")),
        1,
    )
    next_round = int(state.get("next_round") or (current_round + 1))
    if next_round <= current_round:
        next_round = current_round + 1
        state["next_round"] = next_round
        save_top4_state(state)
    round_no = request.args.get("round", type=int)
    if round_no is None:
        round_no = current_round
    return round_no, current_round, state


def _build_lineups(round_no: int, current_round: int, state: dict) -> dict:
    mapping = load_player_map()
    players = load_top4_players()
    pidx = top4_players_index(players)
    rosters = state.get("rosters") or {}

    cache = _load_round_cache(round_no) if round_no < current_round else {}
    cache_updated = False
    debug: list[str] = []
    debug.append(f"round={round_no} current_round={current_round}")
    debug.append(f"rosters={{{', '.join(f'{m}:{len(r or [])}' for m, r in rosters.items())}}}")
    if cache:
        debug.append(f"cache loaded entries={len(cache)}")
    else:
        debug.append("cache empty")
    print(f"[lineups] start round={round_no} current_round={current_round} cache_entries={len(cache)}")

    schedule = build_schedule()
    gw_rounds: dict[str, int | None] = {}
    for league, rounds in schedule.items():
        match = next((r for r in rounds if r.get("gw") == round_no), None)
        gw_rounds[league] = match.get("round") if match else None
    debug.append(f"gw_rounds={gw_rounds}")
    print(f"[lineups] gw_rounds={gw_rounds}")

    results: dict[str, dict] = {}
    for manager, roster in rosters.items():
        lineup = []
        total = 0
        debug.append(f"manager {manager} roster_size={len(roster or [])}")
        print(f"[lineups] manager {manager} roster_size={len(roster or [])}")
        for item in roster or []:
            fid = str(item.get("playerId") or item.get("id"))
            meta = pidx.get(fid, {})
            pos = item.get("position") or meta.get("position")
            name = meta.get("fullName") or meta.get("shortName") or fid
            league = meta.get("league")
            league_round = gw_rounds.get(league)
            mid = mapping.get(fid)
            pts = 0
            debug.append(f"  player fid={fid} name={name} pos={pos} league={league} league_round={league_round} mid={mid}")
            print(f"[lineups] {manager} player {name} ({pos}) league={league} league_round={league_round} mid={mid}")
            if mid and league_round:
                key = str(mid)
                if round_no < current_round:
                    if key in cache:
                        pts = int(cache[key])
                        debug.append(f"    cache hit mid={mid} pts={pts}")
                        print(f"[lineups] cache hit mid={mid} pts={pts}")
                    else:
                        player = _load_player(mid, debug)
                        round_stats = player.get("round_stats", [])
                        stat = next(
                            (
                                s
                                for s in round_stats
                                if int(s.get("tournament_round_number", 0)) == league_round
                            ),
                            None,
                        )
                        pts = _calc_score(stat, pos) if stat else 0
                        cache[key] = int(pts)
                        cache_updated = True
                        debug.append(f"    cache miss mid={mid} pts={pts}")
                        print(f"[lineups] cache miss mid={mid} pts={pts}")
                elif round_no == current_round:
                    player = _load_player(mid, debug)
                    round_stats = player.get("round_stats", [])
                    stat = next(
                        (
                            s
                            for s in round_stats
                            if int(s.get("tournament_round_number", 0)) == league_round
                        ),
                        None,
                    )
                    pts = _calc_score(stat, pos) if stat else 0
                    debug.append(f"    current round mid={mid} pts={pts}")
                    print(f"[lineups] current round mid={mid} pts={pts}")
                else:
                    pts = 0
                    debug.append(f"    future round mid={mid} pts=0")
                    print(f"[lineups] future round mid={mid} pts=0")
            else:
                debug.append(
                    f"skip fid {fid} name {name}: mid={mid} league_round={league_round}"
                )
                print(f"[lineups] skip fid {fid} name {name}: mid={mid} league_round={league_round}")
            debug.append(f"{manager}: {name} ({pos}) -> {int(pts)}")
            lineup.append({"name": name, "pos": pos, "points": int(pts)})
            total += pts
        lineup.sort(
            key=lambda r: POS_ORDER.get(
                (r.get("pos") or "").strip().upper(), 99
            )
        )
        results[manager] = {"players": lineup, "total": int(total)}
        debug.append(f"manager {manager} total={int(total)}")
        print(f"[lineups] manager {manager} total={int(total)}")

    if cache_updated:
        _save_round_cache(round_no, cache)

    managers = sorted(results.keys())
    debug.append(f"final managers={managers}")
    print(f"[lineups] final managers={managers}")
    return {
        "lineups": results,
        "managers": managers,
        "gw_rounds": gw_rounds,
        "round": round_no,
        "debug": debug,
        "raw_state": state,
    }


@bp.route("/lineups/data")
def lineups_data():
    round_no, current_round, state = _resolve_round()
    print(f"[lineups] lineups_data round={round_no} current_round={current_round}")
    cached = _load_lineups_json(round_no)
    if cached:
        return jsonify(cached)

    with BUILDING_LOCK:
        already = round_no in BUILDING_ROUNDS
        if not already:
            BUILDING_ROUNDS.add(round_no)

    if not already:
        def worker() -> None:
            try:
                data = _build_lineups(round_no, current_round, state)
                _save_lineups_json(round_no, data)
            finally:
                with BUILDING_LOCK:
                    BUILDING_ROUNDS.discard(round_no)

        Thread(target=worker, daemon=True).start()

    return jsonify({"status": "processing", "round": round_no})


@bp.route("/lineups")
def lineups():
    round_no, _, _ = _resolve_round()
    print(f"[lineups] lineups page round={round_no}")
    schedule = build_schedule()
    gw_rounds: dict[str, int | None] = {}
    for league, rounds in schedule.items():
        match = next((r for r in rounds if r.get("gw") == round_no), None)
        gw_rounds[league] = match.get("round") if match else None
    return render_template("top4_lineups.html", round=round_no, gw_rounds=gw_rounds)
