from __future__ import annotations

import json, os, tempfile, traceback
from datetime import datetime, timedelta
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
    TOP4_CACHE_VERSION,
)
from .mantra_store import mantra_store
from .top4_schedule import build_schedule
from .player_map_store import load_player_map, save_player_map
from .top4_score_store import load_top4_score, save_top4_score, SCORE_CACHE_TTL
from .top4_player_info_store import load_player_info, save_player_info

# Routes related to Top-4 statistics and lineups (formerly "mantra").
bp = Blueprint("top4", __name__, url_prefix="/top4")

API_URL = "https://mantrafootball.org/api/players/{id}/stats"

BASE_DIR = Path(__file__).resolve().parent.parent
ROUND_CACHE_DIR = BASE_DIR / "data" / "cache" / "mantra_rounds" / TOP4_CACHE_VERSION
LINEUPS_DIR = BASE_DIR / "data" / "cache" / "top4_lineups" / TOP4_CACHE_VERSION

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
BUILD_ERRORS: dict[int, str] = {}
LINEUPS_CACHE_TTL = timedelta(minutes=30)

# Bump this constant when logic that affects cached Top-4 lineups/scores needs
# to be invalidated (e.g. league overrides for transferred players).
LINEUPS_OVERRIDE_VERSION = "2025-09-override-v1"

# Some players changed leagues after the Top-4 scrape.  Their fantasy points
# should follow the new league schedule even if the upstream API still tags
# them with the old tournament.  Override by stable display name (case
# insensitive) so we don't need to edit state/S3 data manually.
LEAGUE_OVERRIDES = {
    "nick woltemade": "EPL",
    "xavi simons": "EPL",
}


def _apply_league_override(name: str | None, league: str | None) -> str | None:
    if not name:
        return league
    override = LEAGUE_OVERRIDES.get(name.lower())
    return override or league


def _to_int(value) -> int:
    """Safely convert numeric strings like ``"1.0"`` to ``int``.

    The Top-4 statistics API often represents numbers as strings with decimal
    points (e.g. ``"2.0"``).  Direct ``int(...)`` casts would raise ``ValueError``
    which in turn broke score calculations, resulting in zero points for some
    rounds.  This helper normalises such values so the rest of the code can
    operate on integers reliably.
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _s3_rounds_prefix() -> str:
    base = os.getenv("DRAFT_S3_MANTRA_ROUNDS_PREFIX", "mantra_rounds")
    return f"{base.rstrip('/')}/{TOP4_CACHE_VERSION}"


def _s3_key(rnd: int) -> str:
    prefix = _s3_rounds_prefix().strip().strip("/")
    return f"{prefix}/round{int(rnd)}.json"


def _s3_lineups_prefix() -> str:
    base = os.getenv("DRAFT_S3_TOP4_LINEUPS_PREFIX", "top4_lineups")
    return f"{base.rstrip('/')}/{TOP4_CACHE_VERSION}"


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
                if data.get("override_version") == LINEUPS_OVERRIDE_VERSION:
                    payload = data.get("data") or {}
                else:
                    payload = {}
                return {str(k): int(v) for k, v in payload.items()}
    p = ROUND_CACHE_DIR / f"round{int(rnd)}.json"
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if data.get("override_version") == LINEUPS_OVERRIDE_VERSION:
                    payload = data.get("data") or {}
                    return {str(k): int(v) for k, v in payload.items()}
        except Exception:
            pass
    return {}


def _save_round_cache(rnd: int, data: dict) -> None:
    payload = {
        "override_version": LINEUPS_OVERRIDE_VERSION,
        "data": {str(k): int(v) for k, v in data.items()},
    }
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


def _save_lineups_json(rnd: int, data: dict) -> dict:
    """Persist full lineup data for debugging (S3 + local)."""
    payload = dict(data or {})
    payload["cached_at"] = datetime.utcnow().isoformat()
    payload["override_version"] = LINEUPS_OVERRIDE_VERSION
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_lineups_key(rnd)
        if bucket and key and not _s3_put_json(bucket, key, payload):
            print(f"[MANTRA:S3] save_lineups_json fallback rnd={rnd}")
    LINEUPS_DIR.mkdir(parents=True, exist_ok=True)
    p = LINEUPS_DIR / f"round{int(rnd)}.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


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

    def _fresh(data: dict) -> bool:
        ts = data.get("cached_at")
        if not ts:
            return False
        try:
            cached = datetime.fromisoformat(ts)
        except Exception:
            return False
        return datetime.utcnow() - cached < LINEUPS_CACHE_TTL

    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_lineups_key(rnd)
        if bucket and key:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict) and data and _fresh(data):
                if data.get("override_version") != LINEUPS_OVERRIDE_VERSION:
                    return None
                return _sort_payload(data)
    p = LINEUPS_DIR / f"round{int(rnd)}.json"
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data and _fresh(data):
                if data.get("override_version") != LINEUPS_OVERRIDE_VERSION:
                    return None
                return _sort_payload(data)
        except Exception:
            pass
    return None


def _load_player_info_with_fetch(pid: int) -> dict:
    """Load player metadata, fetching from API if necessary."""
    data = load_player_info(pid)
    if data:
        return data
    try:
        resp = requests.get(f"https://mantrafootball.org/api/players/{pid}", timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        print(f"[lineups] Fetched player info for pid={pid}: {data.get('first_name', '')} {data.get('name', '')}")
    except Exception as exc:
        print(f"[MANTRA] info fetch failed pid={pid}: {exc}")
        data = {}
    save_player_info(pid, data)
    return data


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

        # Cache player info if not already stored
        if not load_player_info(pid):
            try:
                info_resp = requests.get(
                    f"https://mantrafootball.org/api/players/{pid}", timeout=10
                )
                info_resp.raise_for_status()
                info_data = info_resp.json().get("data", {})
                save_player_info(pid, info_data)
            except Exception as exc:
                print(f"[MANTRA] info fetch failed pid={pid}: {exc}")

        return stats_data.get("data", {})
    except Exception as exc:
        print(f"[MANTRA] fetch failed pid={pid}: {exc}")
        return {}

def _load_player(
    pid: int, debug: list[str] | None = None, round_no: int | None = None, force_refresh: bool = False
) -> dict:
    """Load cached player stats from disk or fetch from the API.

    The ``round_no`` parameter is kept for backward compatibility.
    With TTL removed, cached responses are always considered fresh unless force_refresh is True.
    """

    data = load_top4_score(pid, force_refresh=force_refresh)
    if data and not force_refresh:
        if debug is not None:
            debug.append(f"cached pid {pid}")
        return data

    data = _fetch_player(pid)
    if debug is not None:
        state = "ok" if data else "empty"
        debug.append(f"refetched pid {pid}: {state}")
    # Persist even empty payloads so repeated failures are visible on disk
    save_top4_score(pid, data)
    return data


def _load_player_info(pid: int) -> dict:
    """Load player metadata from cache or fetch once from the API.

    According to the new requirements the lineup page must always read player
    data from the cached JSON file and only fall back to the remote API when
    the file is missing.  No periodic refresh is performed; once fetched the
    information is stored locally and in S3 for subsequent requests.
    """

    data = load_player_info(pid)
    if data:
        return data

    try:
        resp = requests.get(
            f"https://mantrafootball.org/api/players/{pid}", timeout=10
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}) or {}
    except Exception as exc:
        # If the request fails there is nothing cached yet, so log the error and
        # return an empty payload without writing a cache entry.  The caller may
        # retry later.
        print(f"[MANTRA] info fetch failed pid={pid}: {exc}")
        return {}

    save_player_info(pid, data)
    return data


def _ensure_player_info(state: dict) -> None:
    """Ensure cached metadata exists for all mapped players.

    When lineups are served from a cached file the ``_build_lineups``
    function isn't executed and player information isn't lazily fetched.
    This helper walks through all rosters and triggers ``_load_player_info``
    for every mapped player so that the ``top4_player_info`` directory
    eventually contains a JSON file for each drafted footballer.
    """

    mapping = load_player_map()
    rosters = (state.get("rosters") or {}).values()
    processed = 0
    missing_map: list[str] = []
    missing_info: list[int] = []
    for roster in rosters:
        for item in roster or []:
            fid = str(item.get("playerId") or item.get("id"))
            mid = mapping.get(fid)
            if mid:
                if not _load_player_info(mid):
                    missing_info.append(mid)
                processed += 1
            else:
                missing_map.append(fid)

    # Retry fetching data for players that previously failed to load
    if missing_info:
        retry: list[int] = []
        for mid in missing_info:
            if not _load_player_info(mid):
                retry.append(mid)
        if retry:
            print(f"[PLAYER_INFO] retry failed ids: {', '.join(map(str, retry[:20]))}")

    total = processed + len(missing_map)
    print(
        f"[PLAYER_INFO] total={total} processed={processed} missing_map={len(missing_map)}"
    )
    if missing_map:
        print(f"[PLAYER_INFO] missing ids: {', '.join(missing_map[:20])}")


def _calc_score_breakdown(stat: dict, pos: str) -> tuple[int, list[dict]]:
    """Return score and detailed breakdown for a player's match stats."""

    mins = _to_int(stat.get("played_minutes"))
    score = 0
    breakdown: list[dict] = []

    if mins >= 60:
        score += 2
        breakdown.append({"label": "Минуты ≥60", "points": 2})
    elif mins > 0:
        score += 1
        breakdown.append({"label": "Минуты <60", "points": 1})

    goals = float(stat.get("goals", 0))
    goal_pts = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}
    if goals:
        pts = goal_pts.get(pos, 0) * goals
        score += pts
        breakdown.append({"label": f"Голы ({goals})", "points": int(pts)})

    pen_goals = float(stat.get("scored_penalty", 0))
    if pen_goals:
        pts = goal_pts.get(pos, 0) * pen_goals
        score += pts
        breakdown.append({"label": f"Голы с пенальти ({pen_goals})", "points": int(pts)})

    assists = float(stat.get("assists", 0))
    if assists:
        pts = 3 * assists
        score += pts
        breakdown.append({"label": f"Ассисты ({assists})", "points": int(pts)})

    if stat.get("cleansheet") and mins >= 60:
        cs_pts = 0
        if pos in ("GK", "DEF"):
            cs_pts = 4
        elif pos == "MID":
            cs_pts = 1
        if cs_pts:
            score += cs_pts
            breakdown.append({"label": "Сухой матч", "points": cs_pts})

    if pos == "GK":
        caught = float(stat.get("caught_penalty", 0))
        if caught:
            pts = 5 * caught
            score += pts
            breakdown.append({"label": f"Сейвы пенальти ({caught})", "points": int(pts)})
        saves = _to_int(stat.get("saves"))
        if saves:
            pts = saves // 3
            if pts:
                score += pts
                breakdown.append({"label": f"Сейвы ({saves})", "points": pts})

    missed_pen = _to_int(stat.get("missed_penalty"))
    if missed_pen:
        pts = -2 * missed_pen
        score += pts
        breakdown.append({"label": f"Нереализованные пенальти ({missed_pen})", "points": pts})

    if pos in ("GK", "DEF"):
        conceded = _to_int(stat.get("missed_goals"))
        if conceded:
            pts = -(conceded // 2)
            if pts:
                score += pts
                breakdown.append({"label": f"Пропущенные голы ({conceded})", "points": pts})

    yc = _to_int(stat.get("yellow_card"))
    if yc:
        pts = -yc
        score += pts
        breakdown.append({"label": f"Жёлтые карточки ({yc})", "points": pts})

    rc = _to_int(stat.get("red_card"))
    if rc:
        pts = -3 * rc
        score += pts
        breakdown.append({"label": f"Красные карточки ({rc})", "points": pts})

    return int(score), breakdown


def _calc_score(stat: dict, pos: str) -> int:
    score, _ = _calc_score_breakdown(stat, pos)
    return score


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
    current_round = int(
        next(
            (rd.get("gw") for rounds in schedule.values() for rd in rounds if rd.get("current")),
            1,
        )
        or 1
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

    past_round = current_round <= 0 or round_no < current_round
    cache = _load_round_cache(round_no) if past_round else {}
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
            pl = item.get("player") if isinstance(item, dict) and item.get("player") else item
            fid = str(pl.get("playerId") or pl.get("id"))
            meta = pidx.get(fid, {})
            pos = pl.get("position") or meta.get("position")
            name = pl.get("fullName") or meta.get("fullName") or meta.get("shortName") or fid
            league = pl.get("league") or meta.get("league")
            league = _apply_league_override(name, league)
            league = _apply_league_override(name, league)
            league_round = gw_rounds.get(league)
            mid = mapping.get(fid)
            pts = 0
            stat = None
            breakdown: list[dict] = []
            debug.append(f"  player fid={fid} name={name} pos={pos} league={league} league_round={league_round} mid={mid}")
            print(f"[lineups] {manager} player {name} ({pos}) league={league} league_round={league_round} mid={mid}")
            if mid and league_round:
                key = str(mid)
                if past_round or round_no == current_round:
                    player = _load_player(mid, debug, league_round)
                    round_stats = player.get("round_stats", [])
                    stat = next(
                        (
                            s
                            for s in round_stats
                            if _to_int(s.get("tournament_round_number")) == league_round
                        ),
                        None,
                    )
                    pts, breakdown = _calc_score_breakdown(stat, pos) if stat else (0, [])
                    cached = cache.get(key) if past_round else None
                    if cached != pts:
                        cache[key] = int(pts)
                        cache_updated = True
                    if past_round:
                        msg = "cache hit" if cached is not None else "cache miss"
                        debug.append(f"    {msg} mid={mid} pts={pts}")
                        print(f"[lineups] {msg} mid={mid} pts={pts}")
                    else:
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
            # Read player metadata from cache only.  Any missing entries will be
            # fetched asynchronously after the lineups JSON has been generated.
            info = load_player_info(mid) if mid else {}
            first = (info.get('first_name') or "").strip()
            last = (info.get('name') or "").strip()
            display_name = (
                (info.get('full_name') or "").strip()
                or f"{first} {last}".strip()
                or name
            )
            # Get logo_path from club info
            club_info = info.get('club')
            logo = None
            if isinstance(club_info, dict):
                logo = club_info.get('logo_path')
                
            print(f"[lineups] {display_name}: logo={logo}")
            debug.append(f"{manager}: {display_name} ({pos}) -> {int(pts)}")
            lineup.append(
                {
                    "name": display_name,
                    "logo": logo,
                    "pos": pos,
                    "points": int(pts),
                    "breakdown": breakdown,
                    # expose original stat payload so the frontend can display
                    # raw metrics for the player.  ``league`` and
                    # ``league_round`` are included to build a descriptive
                    # header in the statistics popup (e.g. ``GW5 Тур 3``).
                    "stat": stat,
                    "league": league,
                    "league_round": league_round,
                }
            )
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


def _build_results(state: dict) -> dict:
    mapping = load_player_map()
    players = load_top4_players()
    pidx = top4_players_index(players)
    rosters = state.get("rosters") or {}

    schedule = build_schedule()
    round_to_gw: dict[str, dict[int, int]] = {}
    for league, rounds in schedule.items():
        rmap: dict[int, int] = {}
        for r in rounds:
            rnd = _to_int(r.get("round"))
            gw = _to_int(r.get("gw"))
            if rnd and gw:
                rmap[rnd] = gw
        round_to_gw[league] = rmap

    results: dict[str, dict] = {}
    for manager, roster in rosters.items():
        lineup = []
        total = 0
        for item in roster or []:
            pl = item.get("player") if isinstance(item, dict) and item.get("player") else item
            fid = str(pl.get("playerId") or pl.get("id"))
            meta = pidx.get(fid, {})
            pos = pl.get("position") or meta.get("position")
            name = (
                pl.get("fullName")
                or meta.get("fullName")
                or meta.get("shortName")
                or fid
            )
            league = pl.get("league") or meta.get("league")
            mid = mapping.get(fid)
            pts = 0
            extra = 0
            breakdown: list[dict] = []
            if mid:
                player = _load_player(mid)
                round_stats = player.get("round_stats") or []
                for stat in round_stats:
                    rnd = _to_int(stat.get("tournament_round_number"))
                    gw = round_to_gw.get(league, {}).get(rnd)
                    score = _calc_score(stat, pos) if pos else 0
                    label = f"GW{gw}" if gw else f"R{rnd}"
                    breakdown.append({"label": label, "points": int(score)})
                    if gw:
                        pts += score
                    else:
                        extra += score
            info = load_player_info(mid) if mid else {}
            first = (info.get("first_name") or "").strip()
            last = (info.get("name") or "").strip()
            display_name = (
                (info.get("full_name") or "").strip()
                or f"{first} {last}".strip()
                or name
            )
            logo = (info.get("club") or {}).get("logo_path")
            breakdown.sort(key=lambda b: b["label"])
            entry = {
                "name": display_name,
                "logo": logo,
                "pos": pos,
                "points": int(pts),
                "breakdown": breakdown,
            }
            if extra:
                entry["extra_points"] = int(extra)
            lineup.append(entry)
            total += pts
        lineup.sort(
            key=lambda r: POS_ORDER.get((r.get("pos") or "").strip().upper(), 99)
        )
        results[manager] = {"players": lineup, "total": int(total)}

    managers = sorted(
        results.keys(), key=lambda m: (-results[m]["total"], m)
    )
    return {"lineups": results, "managers": managers}


@bp.route("/lineups/data")
def lineups_data():
    round_no, current_round, state = _resolve_round()
    print(f"[lineups] lineups_data round={round_no} current_round={current_round}")
    err = BUILD_ERRORS.get(round_no)
    if err:
        return jsonify({"status": "error", "round": round_no, "error": err})
    cached = _load_lineups_json(round_no)
    if cached:
        Thread(target=_ensure_player_info, args=(state,), daemon=True).start()
        return jsonify(cached)

    with BUILDING_LOCK:
        already = round_no in BUILDING_ROUNDS
        if not already:
            BUILDING_ROUNDS.add(round_no)
            BUILD_ERRORS.pop(round_no, None)

    if not already:
        def worker() -> None:
            try:
                data = _build_lineups(round_no, current_round, state)
                _save_lineups_json(round_no, data)
                # Fetch missing player information in the background without
                # blocking the response served to the client.
                Thread(target=_ensure_player_info, args=(state,), daemon=True).start()
            except Exception as exc:
                BUILD_ERRORS[round_no] = str(exc)
                traceback.print_exc()
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


@bp.route("/results/data")
def results_data():
    state = load_top4_state()
    data = _build_results(state)
    return jsonify(data)


@bp.route("/results")
def results():
    return render_template("top4_results.html")


# Global status for stats refresh
stats_refresh_status = {
    'running': False,
    'progress': 0,
    'message': '',
    'total': 0,
    'refreshed': 0
}

def run_stats_refresh():
    """Run stats refresh in background"""
    global stats_refresh_status
    
    try:
        stats_refresh_status['running'] = True
        stats_refresh_status['progress'] = 0
        stats_refresh_status['message'] = 'Starting stats refresh...'
        
        state = load_top4_state()
        mapping = load_player_map()
        rosters = state.get("rosters") or {}
        
        # Collect all player IDs from rosters
        all_player_ids = set()
        for roster in rosters.values():
            for item in roster or []:
                pl = item.get("player") if isinstance(item, dict) and item.get("player") else item
                fid = str(pl.get("playerId") or pl.get("id"))
                if fid in mapping:
                    all_player_ids.add(mapping[fid])
        
        stats_refresh_status['total'] = len(all_player_ids)
        stats_refresh_status['refreshed'] = 0
        
        # Refresh stats for all players
        for i, pid in enumerate(all_player_ids):
            try:
                _load_player(int(pid), force_refresh=True)
                stats_refresh_status['refreshed'] += 1
                stats_refresh_status['progress'] = int((i + 1) / len(all_player_ids) * 100)
                stats_refresh_status['message'] = f'Refreshed {stats_refresh_status["refreshed"]}/{stats_refresh_status["total"]} players'
            except Exception as e:
                print(f"[TOP4] Failed to refresh player {pid}: {e}")
        
        stats_refresh_status['progress'] = 100
        stats_refresh_status['message'] = f'Completed: refreshed {stats_refresh_status["refreshed"]}/{stats_refresh_status["total"]} players'
        
    except Exception as e:
        print(f"[TOP4] Stats refresh error: {e}")
        stats_refresh_status['message'] = f'Error: {str(e)}'
    finally:
        stats_refresh_status['running'] = False

@bp.route("/refresh_stats", methods=["POST"])
def refresh_stats():
    """Refresh statistics for all Top-4 players."""
    if not session.get("godmode"):
        return jsonify({"error": "Access denied"}), 403
    
    if stats_refresh_status['running']:
        return jsonify({'error': 'Stats refresh already running'}), 400
    
    # Start refresh in background
    thread = Thread(target=run_stats_refresh)
    thread.daemon = True
    thread.start()
    
    return jsonify({'message': 'Stats refresh started', 'status': 'running'})

@bp.route("/refresh_stats/status", methods=["GET"])
def refresh_stats_status():
    """Get stats refresh status"""
    if not session.get("godmode"):
        return jsonify({"error": "Access denied"}), 403
    
    return jsonify(stats_refresh_status)
