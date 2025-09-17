from __future__ import annotations
import json, os, tempfile, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime

import requests

from .config import EPL_POSITION_LIMITS, EPL_USERS

# === S3 ===
try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:  # boto3 может быть не установлен локально
    boto3 = None
    BotoConfig = None

# -------- Paths / constants --------
BASE_DIR = Path(__file__).resolve().parent.parent

# Локальные файлы (фолбэк)
EPL_STATE = BASE_DIR / "draft_state_epl.json"
# bootstrap сохраняется во временный каталог
EPL_FPL   = Path(tempfile.gettempdir()) / "players_fpl_bootstrap.json"
WISHLIST_DIR = BASE_DIR / "data" / "wishlist" / "epl"

# Кеш результатов туров (по игрокам)
GW_STATS_DIR = BASE_DIR / "data" / "cache" / "gw_stats"
GW_STATS_DIR.mkdir(parents=True, exist_ok=True)

# Кеш element-summary
CACHE_DIR = BASE_DIR / "data" / "cache" / "element_summary"
CACHE_TTL_SEC = 24 * 3600  # 24h

# FPL bootstrap
FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
BOOTSTRAP_TTL_SEC = 3600  # 1 час

POS_CANON = {
    "Goalkeeper": "GK", "GK": "GK",
    "Defender": "DEF", "DEF": "DEF",
    "Midfielder": "MID", "MID": "MID",
    "Forward": "FWD", "FWD": "FWD",
}

# Лимиты по позициям берём из конфигурации EPL_POSITION_LIMITS
DEFAULT_SLOTS = {
    "GK": EPL_POSITION_LIMITS.get("Goalkeeper", 0),
    "DEF": EPL_POSITION_LIMITS.get("Defender", 0),
    "MID": EPL_POSITION_LIMITS.get("Midfielder", 0),
    "FWD": EPL_POSITION_LIMITS.get("Forward", 0),
}
EPL_MANAGER_SET = {m for m in EPL_USERS}
EPL_TOTAL_ROUNDS = sum(DEFAULT_SLOTS.values())
LAST_SEASON = "2024/25"

# Расписание трансферных окон: gameweek -> число раундов
TRANSFER_SCHEDULE: Dict[int, int] = {
    3: 2,
    10: 1,
    17: 1,
    24: 2,
    29: 1,
    34: 1,
}

# -------- JSON I/O (локально) --------
def json_load(p: Path) -> Any:
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

def json_dump_atomic(p: Path, data: Any):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="state_", suffix=".json", dir=str(p.parent))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _snake_order(users: List[str], rounds: int) -> List[str]:
    order: List[str] = []
    for rnd in range(rounds):
        seq = users if rnd % 2 == 0 else list(reversed(users))
        order.extend(seq)
    return order

# ======================
#        S3 I/O
# ======================
def _s3_enabled() -> bool:
    """Return True when S3 mirroring should be attempted.

    Раньше требовалось указывать ``DRAFT_S3_STATE_KEY``; если переменная
    отсутствовала, то даже кеши очков не синхронизировались с S3. Теперь
    достаточно самого bucket — ключи берутся из специализированных хелперов,
    что гарантирует загрузку всех JSON со стейтом и начисленными очками в S3.
    """
    return bool(_s3_bucket())

def _s3_bucket() -> Optional[str]:
    return os.getenv("DRAFT_S3_BUCKET")

def _s3_state_key() -> Optional[str]:
    specific = os.getenv("DRAFT_S3_EPL_STATE_KEY")
    if specific:
        return specific.strip()
    key = os.getenv("DRAFT_S3_STATE_KEY")
    if key:
        candidate = key.strip()
        if "epl" in candidate.lower():
            return candidate
    legacy = os.getenv("EPL_S3_STATE_KEY")
    if legacy:
        return legacy.strip()
    # Фолбэк сохраняет историческое имя файла, чтобы не потерять уже загруженный стейт.
    return "draft_state_epl.json"

def _s3_wishlist_prefix() -> str:
    # Можно переопределить префикс через ENV, по умолчанию wishlist/epl
    return os.getenv("DRAFT_S3_WISHLIST_PREFIX", "wishlist/epl")

def _s3_gwstats_prefix() -> str:
    # Префикс для кеша результатов туров
    return os.getenv("DRAFT_S3_GWSTATS_PREFIX", "gw_stats")

def _s3_bootstrap_key() -> Optional[str]:
    return os.getenv("DRAFT_S3_BOOTSTRAP_KEY")

def _s3_client():
    if not boto3:
        return None
    cfg = BotoConfig(
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=5,
        read_timeout=8,
    )
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("s3", region_name=region, config=cfg)

def _s3_get_json(bucket: str, key: str) -> Optional[dict]:
    cli = _s3_client()
    if not cli:
        return None
    try:
        obj = cli.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        return json.loads(body.decode("utf-8"))
    except Exception as e:
        # Missing objects are a normal scenario – silently treat them as cache
        # misses instead of polluting logs with ``NoSuchKey`` errors.
        code = getattr(getattr(e, "response", {}), "get", lambda *a, **k: {})("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            return None
        print(f"[EPL:S3] get_object failed: s3://{bucket}/{key} -> {e}")
        return None

def _s3_put_json(bucket: str, key: str, data: dict) -> bool:
    cli = _s3_client()
    if not cli:
        return False
    try:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        cli.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            CacheControl="no-cache",
        )
        return True
    except Exception as e:
        print(f"[EPL:S3] put_object failed: s3://{bucket}/{key} -> {e}")
        return False

# -------- Bootstrap fetch/refresh (1h TTL) --------
def ensure_fpl_bootstrap_fresh() -> dict:
    """
    Возвращает свежие данные bootstrap-static.
    Если локальный файл отсутствует/старше 1 часа/некорректен — скачивает новый
    и перезаписывает. Файл кешируется во временном каталоге и при наличии
    переменных окружения DRAFT_S3_BUCKET и DRAFT_S3_BOOTSTRAP_KEY также
    сохраняется в S3.
    """
    bucket = _s3_bucket()
    key = _s3_bootstrap_key()
    try:
        if EPL_FPL.exists():
            age = time.time() - EPL_FPL.stat().st_mtime
            if age <= BOOTSTRAP_TTL_SEC:
                data = json_load(EPL_FPL)
                if isinstance(data, dict) and data.get("elements"):
                    return data
        if bucket and key:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict) and data.get("elements"):
                json_dump_atomic(EPL_FPL, data)
                return data
        r = requests.get(FPL_BOOTSTRAP_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("elements"):
            json_dump_atomic(EPL_FPL, data)
            if bucket and key:
                _s3_put_json(bucket, key, data)
            return data
    except Exception as e:
        print(f"[EPL] Failed to fetch bootstrap-static: {e}")
    data = json_load(EPL_FPL)
    if isinstance(data, dict):
        return data
    if bucket and key:
        data = _s3_get_json(bucket, key)
        if isinstance(data, dict):
            return data
    return {}

# -------- Players (bootstrap → internal model) --------
def players_from_fpl(bootstrap: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(bootstrap, dict):
        return out
    elements = bootstrap.get("elements") or []
    teams = {t.get("id"): t.get("name") for t in (bootstrap.get("teams") or [])}
    short = {t.get("id"): (t.get("short_name") or "").upper() for t in (bootstrap.get("teams") or [])}
    pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    for e in elements:
        pid = e.get("id")
        if pid is None: continue
        first = (e.get("first_name") or "").strip()
        second = (e.get("second_name") or "").strip()
        web = (e.get("web_name") or second or "").strip()
        full = f"{first} {second}".strip()
        team_id = e.get("team")
        club_full = teams.get(team_id) or str(team_id)
        club_abbr = short.get(team_id) or (club_full or "").upper()
        out.append({
            "playerId": int(pid),
            "shortName": web,
            "fullName": full,
            "clubName": club_abbr,
            "clubFull": club_full,
            "position": pos_map.get(e.get("element_type")),
            "price": (e.get("now_cost") / 10.0) if isinstance(e.get("now_cost"), (int, float)) else None,
            "teamId": int(team_id) if team_id is not None else None,
            "status": e.get("status"),
            "news": e.get("news"),
            "chance": e.get("chance_of_playing_next_round"),
            "stats": {
                "minutes": e.get("minutes"),
                "goals": e.get("goals_scored"),
                "assists": e.get("assists"),
                "cs": e.get("clean_sheets"),
                "points": e.get("total_points"),
            },
        })
    return out

def players_index(plist: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(p["playerId"]): p for p in plist}

def nameclub_index(plist: List[Dict[str, Any]]) -> Dict[Tuple[str,str], Set[str]]:
    def norm(s: Optional[str]) -> str:
        if not s: return ""
        return " ".join(str(s).replace(".", " ").split()).lower()
    idx: Dict[Tuple[str,str], Set[str]] = {}
    for p in plist:
        pid = str(p["playerId"])
        club = (p.get("clubName") or "").upper()
        for nm in (p.get("shortName"), p.get("fullName")):
            key = (norm(nm), club)
            if not key[0] or not club: continue
            idx.setdefault(key, set()).add(pid)
    return idx

def photo_url_for(pid: int) -> Optional[str]:
    """Return player photo URL or placeholder if missing."""
    data = ensure_fpl_bootstrap_fresh()
    for e in (data.get("elements") or []):
        if int(e.get("id", -1)) == int(pid):
            code = e.get("code")
            if code:
                return (
                    "https://resources.premierleague.com/"
                    f"premierleague/photos/players/110x140/p{code}.png"
                )
    return (
        "https://resources.premierleague.com/"
        "premierleague25/photos/players/110x140/placeholder.png"
    )


# -------- Fixtures and points --------
def fixtures_for_gw(gw: int, bootstrap: Optional[Dict[str, Any]] = None) -> Dict[int, str]:
    """Return mapping of teamId -> '(H) OPP' or '(A) OPP' for given gameweek."""
    if bootstrap is None:
        bootstrap = ensure_fpl_bootstrap_fresh()
    teams = {int(t.get("id")): (t.get("short_name") or "").upper() for t in (bootstrap.get("teams") or [])}
    mapping: Dict[int, str] = {}
    try:
        url = f"https://fantasy.premierleague.com/api/fixtures/?event={int(gw)}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        fixtures = r.json() or []
    except Exception:
        fixtures = []
    for fxt in fixtures:
        try:
            home = int(fxt.get("team_h"))
            away = int(fxt.get("team_a"))
        except Exception:
            continue
        home_opp = teams.get(away, "")
        away_opp = teams.get(home, "")
        if home_opp:
            mapping[home] = f"(H) {home_opp}"
        if away_opp:
            mapping[away] = f"(A) {away_opp}"
    return mapping

def points_for_gw(gw: int, pidx: Optional[Dict[str, Any]] = None) -> Dict[int, Dict[str, Any]]:
    """
    Return mapping playerId -> {points, minutes, status} for given gameweek.

    status is one of: "not_started", "in_progress", "finished" (finished_provisional).
    If ``pidx`` is provided it is used to determine teamId for players who
    have not yet played (``minutes`` == 0).
    """

    # Cached stats for finished gameweeks
    cached = load_gw_stats(gw)
    if cached:
        return cached

    # Fetch fixture statuses for the gameweek
    fixtures_by_id: Dict[int, str] = {}
    fixtures_by_team: Dict[int, str] = {}
    try:
        url_fx = f"https://fantasy.premierleague.com/api/fixtures/?event={int(gw)}"
        r_fx = requests.get(url_fx, timeout=10)
        r_fx.raise_for_status()
        fxts = r_fx.json() or []
    except Exception:
        fxts = []
    for f in fxts:
        try:
            fid = int(f.get("id"))
        except Exception:
            continue
        status = "not_started"
        if f.get("finished_provisional"):
            status = "finished"
        elif f.get("started"):
            status = "in_progress"
        fixtures_by_id[fid] = status
        home = f.get("team_h")
        away = f.get("team_a")
        if home is not None:
            fixtures_by_team[int(home)] = status
        if away is not None:
            fixtures_by_team[int(away)] = status

    # Fetch live player stats
    url = f"https://fantasy.premierleague.com/api/event/{int(gw)}/live/"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json() or {}
    except Exception:
        data = {}

    stats: Dict[int, Dict[str, Any]] = {}
    for el in data.get("elements", []):
        pid = el.get("id")
        if pid is None:
            continue
        estats = el.get("stats") or {}
        try:
            points = int(estats.get("total_points") or 0)
        except Exception:
            points = 0
        try:
            minutes = int(estats.get("minutes") or 0)
        except Exception:
            minutes = 0

        # Determine fixture status for the player
        fixture_id = None
        explain = el.get("explain") or []
        if explain:
            try:
                fixture_id = int((explain[0] or {}).get("fixture"))
            except Exception:
                fixture_id = None
        status = "not_started"
        if fixture_id and fixture_id in fixtures_by_id:
            status = fixtures_by_id[fixture_id]
        elif pidx is not None and str(pid) in pidx:
            team_id = pidx[str(pid)].get("teamId")
            if team_id is not None:
                status = fixtures_by_team.get(int(team_id), "not_started")

        stats[int(pid)] = {"points": points, "minutes": minutes, "status": status}

    # Save cache if all fixtures finished
    if fixtures_by_id and all(s == "finished" for s in fixtures_by_id.values()):
        save_gw_stats(gw, stats)

    return stats

# ======================
#      STATE (S3)
# ======================
def load_state() -> Dict[str, Any]:
    """
    Загружаем состояние из S3 (если настроено), иначе — из локального файла.
    """
    if _s3_enabled():
        bucket = _s3_bucket()
        key    = _s3_state_key()
        data = _s3_get_json(bucket, key) if bucket and key else None
        if isinstance(data, dict):
            state = data
        else:
            state = {}
    else:
        state = json_load(EPL_STATE) or {}

    # normalize defaults
    _normalize_epl_state(state)
    return state


def _normalize_epl_state(state: Dict[str, Any]) -> None:
    changed = False

    rosters_raw = state.get("rosters")
    rosters: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(rosters_raw, dict):
        for manager in EPL_USERS:
            arr = rosters_raw.get(manager)
            if isinstance(arr, list):
                rosters[manager] = arr
            else:
                rosters[manager] = []
    else:
        rosters = {manager: [] for manager in EPL_USERS}
    if rosters != rosters_raw:
        state["rosters"] = rosters
        changed = True

    roster_id_map: Dict[str, Set[int]] = {}
    roster_order_map: Dict[str, List[int]] = {}
    for manager, arr in rosters.items():
        ids: Set[int] = set()
        order: List[int] = []
        for pl in arr:
            try:
                pid = int(pl.get("playerId") or pl.get("id"))
            except Exception:
                continue
            if pid in ids:
                continue
            ids.add(pid)
            order.append(pid)
        roster_id_map[manager] = ids
        roster_order_map[manager] = order

    picks = state.get("picks")
    if isinstance(picks, list):
        filtered = [row for row in picks if (row or {}).get("user") in EPL_MANAGER_SET]
        if filtered != picks:
            state["picks"] = filtered
            changed = True
    else:
        state["picks"] = []
        changed = True

    if "draft_started_at" not in state:
        state["draft_started_at"] = None
        changed = True
    lineups_raw = state.get("lineups")
    if not isinstance(lineups_raw, dict):
        lineups_raw = {}
        state["lineups"] = lineups_raw
        changed = True

    for manager in EPL_USERS:
        entries = lineups_raw.get(manager)
        if not isinstance(entries, dict):
            lineups_raw[manager] = {} if entries is None else {}
            changed = True
            entries = lineups_raw[manager]
        roster_order = roster_order_map.get(manager, [])
        removable: List[str] = []
        for gw_key, payload in list(entries.items()):
            if not isinstance(payload, dict):
                removable.append(gw_key)
                continue
            players_raw = payload.get("players")
            bench_raw = payload.get("bench")
            players: List[int] = []
            seen: Set[int] = set()
            if isinstance(players_raw, (list, tuple)):
                for val in players_raw:
                    try:
                        pid = int(val)
                    except Exception:
                        continue
                    if pid in seen:
                        continue
                    players.append(pid)
                    seen.add(pid)
            bench: List[int] = []
            if isinstance(bench_raw, (list, tuple)):
                for val in bench_raw:
                    try:
                        pid = int(val)
                    except Exception:
                        continue
                    if pid in seen:
                        continue
                    bench.append(pid)
                    seen.add(pid)
            # Fill starters up to 11 using roster order
            for pid in roster_order:
                if len(players) >= 11:
                    break
                if pid in seen:
                    continue
                players.append(pid)
                seen.add(pid)

            # Auto-extend bench with remaining roster players (preserve already recorded bench order)
            for pid in roster_order:
                if pid in players or pid in bench:
                    continue
                bench.append(pid)

            # If starters still short (ростер < 11), top up from bench
            while len(players) < 11 and bench:
                pid = bench.pop(0)
                if pid in players:
                    continue
                players.append(pid)
            # Ensure bench remains unique after topping up
            uniq_bench: List[int] = []
            seen_bench: Set[int] = set(players)
            for pid in bench:
                if pid in seen_bench:
                    continue
                seen_bench.add(pid)
                uniq_bench.append(pid)
            bench = uniq_bench

            normalized = dict(payload)
            if normalized.get("players") != players:
                normalized["players"] = players
            if normalized.get("bench") != bench:
                normalized["bench"] = bench
            if normalized != payload:
                entries[gw_key] = normalized
                changed = True
        for key in removable:
            entries.pop(key, None)
            changed = True

    extra_keys = set(lineups_raw.keys()) - set(EPL_USERS)
    for key in extra_keys:
        lineups_raw.pop(key, None)
        changed = True

    transfer_raw = state.get("transfer")
    if not isinstance(transfer_raw, dict):
        transfer = {}
        changed = True
    else:
        transfer = dict(transfer_raw)
    pending_out = transfer.get("pending_out")
    if isinstance(pending_out, dict):
        filtered = {m: pending_out.get(m) for m in EPL_USERS if pending_out.get(m) is not None}
        if filtered != pending_out:
            transfer["pending_out"] = filtered
            changed = True
    order = transfer.get("order")
    if isinstance(order, list):
        filtered_order = [m for m in order if m in EPL_MANAGER_SET]
        if filtered_order != order:
            transfer["order"] = filtered_order
            changed = True
    if transfer != transfer_raw:
        state["transfer"] = transfer
        changed = True

    limits = state.get("limits")
    desired_slots = dict(DEFAULT_SLOTS)
    desired_limits = {"Slots": desired_slots, "Max from club": 3}
    if not isinstance(limits, dict) or limits.get("Slots") != desired_slots or limits.get("Max from club") != 3:
        state["limits"] = desired_limits
        changed = True

    order = state.get("draft_order") or []
    if not isinstance(order, list):
        order = []
    base_order = _snake_order(EPL_USERS, EPL_TOTAL_ROUNDS)
    if len(order) != len(base_order) or any(name not in EPL_MANAGER_SET for name in order):
        state["draft_order"] = base_order
        changed = True
        order = base_order

    try:
        idx = int(state.get("current_pick_index", 0))
    except Exception:
        idx = 0
        state["current_pick_index"] = 0
        changed = True
    if "current_pick_index" not in state:
        state["current_pick_index"] = idx
        changed = True
    if idx < 0:
        idx = 0
        state["current_pick_index"] = 0
        changed = True
    if idx > len(order):
        idx = len(order)
        state["current_pick_index"] = idx
        changed = True

    next_user = order[idx] if idx < len(order) else None
    if state.get("next_user") != next_user:
        state["next_user"] = next_user
        changed = True

    if EPL_USERS:
        next_round = (idx // len(EPL_USERS)) + 1
        if state.get("next_round") != next_round:
            state["next_round"] = next_round
            changed = True

    if idx < len(order) and state.get("draft_completed"):
        state["draft_completed"] = False
        changed = True

    if changed:
        save_state(state)

def save_state(state: Dict[str, Any]):
    """
    Сохраняем состояние в S3 (если настроено), иначе — локально.
    """
    if _s3_enabled():
        bucket = _s3_bucket()
        key    = _s3_state_key()
        if bucket and key:
            ok = _s3_put_json(bucket, key, state)
            if ok:
                return
        # если не удалось — не роняем приложение, пишем локально как фолбэк
        print("[EPL:S3] save_state fallback to local file")
    json_dump_atomic(EPL_STATE, state)

def who_is_on_clock(state: Dict[str, Any]) -> Optional[str]:
    try:
        order = state.get("draft_order") or []
        idx = int(state.get("current_pick_index", 0))
        return order[idx] if 0 <= idx < len(order) else None
    except Exception:
        return None

def slots_from_state(state: Dict[str, Any]) -> Dict[str, int]:
    limits = state.get("limits") or {}
    slots = (limits.get("Slots") if isinstance(limits, dict) else None) or {}
    merged = DEFAULT_SLOTS.copy()
    if isinstance(slots, dict):
        for k, v in slots.items():
            if k in merged and isinstance(v, int) and v >= 0:
                merged[k] = v
    return merged

def picked_fpl_ids_from_state(
    state: Dict[str, Any],
    idx: Dict[Tuple[str,str], Set[str]]
) -> Set[str]:
    def norm(s: Optional[str]) -> str:
        if not s: return ""
        return " ".join(str(s).replace(".", " ").split()).lower()
    picked: Set[str] = set()

    # Build auxiliary index by name regardless of club to handle transfers
    name_idx: Dict[str, Set[str]] = {}
    for (nm, _club), ids in idx.items():
        name_idx.setdefault(nm, set()).update(ids)

    def add(pl: Dict[str, Any]):
        # Try by explicit playerId first – it's the most reliable
        pid = pl.get("player_id") or pl.get("playerId") or pl.get("id")
        if pid:
            picked.add(str(pid))
            return

        nm = norm(pl.get("player_name") or pl.get("fullName"))
        club = (pl.get("clubName") or "").upper()

        # Lookup by name+club
        if nm and club:
            ids = idx.get((nm, club))
            if ids:
                picked.update(ids)
                return

        # Fallback: lookup by name only (player may have changed club)
        if nm:
            ids = name_idx.get(nm)
            if ids:
                picked.update(ids)

    for arr in (state.get("rosters") or {}).values():
        if isinstance(arr, list):
            for pl in arr:
                if isinstance(pl, dict):
                    add(pl)
    for row in (state.get("picks") or []):
        pl = (row or {}).get("player") or {}
        if isinstance(pl, dict):
            add(pl)
    return picked

def annotate_can_pick(players: List[Dict[str, Any]], state: Dict[str, Any], current_user: Optional[str]) -> None:
    if not current_user:
        for p in players: p["canPick"] = False
        return
    transfer_state = state.get("transfer") or {}
    transfer_active = bool(transfer_state.get("active"))
    pending_pos = None
    if transfer_active:
        on_clock = transfer_current_manager(state) == current_user
        draft_completed = False
        pending = (transfer_state.get("pending_out") or {}).get(current_user)
        if isinstance(pending, dict):
            pending_pos = POS_CANON.get(pending.get("pos") or pending.get("position"))
    else:
        draft_completed = bool(state.get("draft_completed", False))
        on_clock = (state.get("next_user") or who_is_on_clock(state)) == current_user
    if draft_completed or not on_clock:
        for p in players: p["canPick"] = False
        return
    roster = (state.get("rosters") or {}).get(current_user, []) or []
    slots = slots_from_state(state)
    max_from_club = (state.get("limits") or {}).get("Max from club", 3)
    pos_counts = {"GK":0, "DEF":0, "MID":0, "FWD":0}
    club_counts: Dict[str,int] = {}
    for pl in roster:
        pos = POS_CANON.get(pl.get("position")) or pl.get("position")
        if pos in pos_counts: pos_counts[pos] += 1
        club = (pl.get("clubName") or "").upper()
        if club: club_counts[club] = club_counts.get(club, 0) + 1
    for p in players:
        pos = p.get("position")
        club = (p.get("clubName") or "").upper()
        if transfer_active and pending_pos and pos != pending_pos:
            p["canPick"] = False
            continue
        can_pos = pos in slots and pos_counts.get(pos, 0) < slots[pos]
        can_club = club_counts.get(club, 0) < max_from_club if club else True
        p["canPick"] = bool(can_pos and can_club)


# -------- Transfers helpers --------
def start_transfer_window(state: Dict[str, Any], standings: List[Dict[str, Any]], gw: int) -> bool:
    """Запускает трансферное окно после завершения gw.
    Возвращает True, если окно было запущено."""
    rounds = TRANSFER_SCHEDULE.get(int(gw), 0)
    if rounds <= 0:
        return False
    t = state.setdefault("transfer", {})
    if t.get("active") and t.get("gw") == gw:
        return False
    order = [r.get("manager") for r in standings[::-1]]
    t.update({
        "active": True,
        "gw": gw,
        "round": 1,
        "total_rounds": rounds,
        "order": order,
        "index": 0,
        "history": t.get("history", []),
    })
    save_state(state)
    return True


def transfer_current_manager(state: Dict[str, Any]) -> Optional[str]:
    t = state.get("transfer") or {}
    if not t.get("active"):
        return None
    order = t.get("order") or []
    idx = int(t.get("index", 0))
    return order[idx] if 0 <= idx < len(order) else None


def advance_transfer_turn(state: Dict[str, Any]) -> None:
    t = state.get("transfer") or {}
    if not t.get("active"):
        return
    order = t.get("order") or []
    idx = int(t.get("index", 0)) + 1
    if idx >= len(order):
        rnd = int(t.get("round", 1)) + 1
        if rnd > int(t.get("total_rounds", 1)):
            t.clear()
            t["active"] = False
        else:
            t["round"] = rnd
            t["index"] = 0
    else:
        t["index"] = idx
    save_state(state)


def record_transfer(
    state: Dict[str, Any],
    manager: str,
    out_pid: Optional[int],
    in_player: Dict[str, Any],
) -> None:
    """Записывает трансфер игрока. out_pid может быть None, если игрок был
    удалён из состава ранее (например, до фикса багов). В этом случае
    выполняется лишь добавление нового игрока с проверкой лимитов.
    """
    t = state.setdefault("transfer", {})
    history = t.setdefault("history", [])
    event = {
        "gw": t.get("gw"),
        "round": t.get("round"),
        "manager": manager,
        "out": int(out_pid) if out_pid is not None else None,
        "in": in_player,
        "ts": datetime.utcnow().isoformat(timespec="seconds"),
    }
    history.append(event)
    try:
        from .transfer_store import append_transfer
        append_transfer(event)
    except Exception:
        pass
    rosters = state.setdefault("rosters", {})
    roster = rosters.setdefault(manager, [])
    if out_pid is not None:
        roster = [p for p in roster if int(p.get("playerId") or p.get("id")) != int(out_pid)]
    roster.append(in_player)
    rosters[manager] = roster
    save_state(state)

# -------- element-summary cache --------
def cache_path_for(pid: int) -> Path: return CACHE_DIR / f"{pid}.json"
def cache_valid(p: Path) -> bool:
    if not p.exists(): return False
    try: return (time.time() - p.stat().st_mtime) < CACHE_TTL_SEC
    except Exception: return False

def fetch_element_summary(pid: int) -> Dict[str, Any]:
    p = cache_path_for(pid)
    if cache_valid(p):
        data = json_load(p) or {}
        return data if isinstance(data, dict) else {}
    url = f"https://fantasy.premierleague.com/api/element-summary/{pid}/"
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        data = r.json()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        json_dump_atomic(p, data)
        return data if isinstance(data, dict) else {}
    except Exception:
        return json_load(p) or {}

def fp_last_from_summary(summary: Dict[str, Any]) -> int:
    for row in (summary.get("history_past") or []):
        if (row.get("season_name") or "").strip() == LAST_SEASON:
            try: return int(row.get("total_points") or 0)
            except Exception: return 0
    return 0

# -------- status context --------
def build_status_context() -> Dict[str, Any]:
    state = load_state()
    limits = state.get("limits") or {}

    picks: List[Dict[str, Any]] = []
    for row in state.get("picks", []):
        pl = row.get("player") or {}
        picks.append({
            "round": row.get("round"),
            "user": row.get("user"),
            "player_name": pl.get("player_name") or pl.get("fullName"),
            "club": pl.get("clubName"),
            "pos": POS_CANON.get(pl.get("position")) or pl.get("position"),
            "ts": row.get("ts"),
        })

    slots = slots_from_state(state)
    squads_grouped: Dict[str, Dict[str, List[Dict[str, Any] | None]]] = {}
    for manager, arr in (state.get("rosters") or {}).items():
        g = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        for pl in arr or []:
            pos = POS_CANON.get(pl.get("position")) or pl.get("position")
            if pos in g:
                g[pos].append({
                    "fullName": pl.get("player_name") or pl.get("fullName"),
                    "position": pos,
                    "clubName": pl.get("clubName"),
                })
        for pos in ("GK", "DEF", "MID", "FWD"):
            need = max(0, slots.get(pos, 0) - len(g[pos]))
            g[pos].extend([None] * need)
        squads_grouped[manager] = g

    return {
        "title": "EPL Fantasy Draft — Состояние драфта",
        "limits": limits,
        "picks": picks,
        "squads_grouped": squads_grouped,
        "draft_completed": bool(state.get("draft_completed", False)),
        "next_user": state.get("next_user") or who_is_on_clock(state),
        "next_round": state.get("next_round"),
        "draft_started_at": state.get("draft_started_at"),
    }

# ======================
#   WISHLIST (S3)
# ======================
def _wishlist_s3_key(manager: str) -> str:
    # Имя файла менеджера: безопасное (без '/')
    safe = manager.replace("/", "_")
    prefix = _s3_wishlist_prefix().strip().strip("/")
    return f"{prefix}/{safe}.json"

def wishlist_load(manager: str) -> List[int]:
    """
    Загружаем wishlist менеджера из S3 (если включено), иначе — локальный файл.
    """
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _wishlist_s3_key(manager)
        data = _s3_get_json(bucket, key) if bucket else None
        if isinstance(data, list):
            try:
                return [int(x) for x in data]
            except Exception:
                return []
        if isinstance(data, dict) and "ids" in data:
            try:
                return [int(x) for x in data.get("ids") or []]
            except Exception:
                return []
        # если ничего нет — пустой список
        return []

    # локальный фолбэк
    p = WISHLIST_DIR / f"{manager.replace('/', '_')}.json"
    try:
        data = json_load(p)
        if isinstance(data, list):
            return [int(x) for x in data]
    except Exception:
        pass
    return []

def wishlist_save(manager: str, ids: List[int]) -> None:
    """
    Сохраняем wishlist менеджера в S3 (если включено), иначе — локально.
    """
    ids_norm = [int(x) for x in ids]
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _wishlist_s3_key(manager)
        payload = ids_norm  # храним просто как JSON-массив
        if bucket and _s3_put_json(bucket, key, payload):
            return
        print(f"[EPL:S3] wishlist_save fallback to local for manager={manager}")

    # локальный фолбэк
    WISHLIST_DIR.mkdir(parents=True, exist_ok=True)
    p = WISHLIST_DIR / f"{manager.replace('/', '_')}.json"
    json_dump_atomic(p, ids_norm)


# --------- GW stats cache (per player) ---------
def _gwstats_s3_key(gw: int) -> str:
    prefix = _s3_gwstats_prefix().strip().strip("/")
    return f"{prefix}/gw{int(gw)}.json"


def load_gw_stats(gw: int) -> Dict[int, Dict[str, Any]]:
    """Загрузить кешированные очки игроков за тур."""
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _gwstats_s3_key(gw)
        if bucket:
            data = _s3_get_json(bucket, key)
            if isinstance(data, dict):
                try:
                    return {int(k): v for k, v in data.items()}
                except Exception:
                    return {}
    p = GW_STATS_DIR / f"gw{int(gw)}.json"
    data = json_load(p)
    if isinstance(data, dict):
        try:
            return {int(k): v for k, v in data.items()}
        except Exception:
            return {}
    return {}


def save_gw_stats(gw: int, stats: Dict[int, Dict[str, Any]]) -> None:
    """Сохранить очки игроков за тур (S3 + локально)."""
    payload = {str(k): v for k, v in stats.items()}
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _gwstats_s3_key(gw)
        if bucket and _s3_put_json(bucket, key, payload):
            pass
        else:
            print(f"[EPL:S3] save_gw_stats fallback gw={gw}")
    json_dump_atomic(GW_STATS_DIR / f"gw{int(gw)}.json", payload)


def gw_info(bootstrap: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[int]]:
    """Вернуть информацию о текущем, следующем и последнем завершённом туре."""
    if bootstrap is None:
        bootstrap = ensure_fpl_bootstrap_fresh()
    events = bootstrap.get("events") or []
    cur = None
    nxt = None
    last_finished = 0
    for ev in events:
        try:
            eid = int(ev.get("id"))
        except Exception:
            continue
        if ev.get("is_current"):
            cur = eid
        if ev.get("is_next"):
            nxt = eid
        if ev.get("finished") and eid > last_finished:
            last_finished = eid
    if nxt is None and cur is not None:
        nxt = cur + 1
    return {"current": cur, "next": nxt, "finished": last_finished}
def build_auto_lineup(roster: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    order: List[int] = []
    pos_map: Dict[int, str] = {}
    for pl in roster:
        try:
            pid = int(pl.get("playerId"))
        except Exception:
            continue
        order.append(pid)
        pos_map[pid] = (pl.get("position") or "").upper()
    return _auto_lineup_from_roster(order, pos_map)


def _auto_lineup_from_roster(order: List[int], pos_map: Dict[int, str]) -> Optional[Dict[str, Any]]:
    if not order:
        return None
    formation_counts = {"GK": 1, "DEF": 4, "MID": 4, "FWD": 2}
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    players: List[int] = []
    bench: List[int] = []
    for pid in order:
        pos = pos_map.get(pid, "")
        if pos in formation_counts and counts[pos] < formation_counts[pos]:
            players.append(pid)
            counts[pos] += 1
        else:
            bench.append(pid)
    while len(players) < 11 and bench:
        players.append(bench.pop(0))
    # if still not enough players, include whatever is available
    if len(players) < 11:
        players.extend(bench)
        bench = []
    formation = f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"
    payload = {
        "formation": formation,
        "players": players,
        "bench": bench,
        "auto": True,
    }
    return payload
