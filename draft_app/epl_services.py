from __future__ import annotations
import json, os, tempfile, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime

import requests

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
EPL_FPL   = BASE_DIR / "players_fpl_bootstrap.json"
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
DEFAULT_SLOTS = {"GK": 3, "DEF": 7, "MID": 8, "FWD": 4}
LAST_SEASON = "2024/25"

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

# ======================
#        S3 I/O
# ======================
def _s3_enabled() -> bool:
    # Включаем, если есть bucket и ключ для state; wishlist использует тот же bucket и prefix.
    return bool(os.getenv("DRAFT_S3_BUCKET") and os.getenv("DRAFT_S3_STATE_KEY"))

def _s3_bucket() -> Optional[str]:
    return os.getenv("DRAFT_S3_BUCKET")

def _s3_state_key() -> Optional[str]:
    return os.getenv("DRAFT_S3_STATE_KEY")

def _s3_wishlist_prefix() -> str:
    # Можно переопределить префикс через ENV, по умолчанию wishlist/epl
    return os.getenv("DRAFT_S3_WISHLIST_PREFIX", "wishlist/epl")

def _s3_gwstats_prefix() -> str:
    # Префикс для кеша результатов туров
    return os.getenv("DRAFT_S3_GWSTATS_PREFIX", "gw_stats")

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
    Если локальный файл отсутствует/старше 1 часа/некорректен — скачивает новый и перезаписывает.
    """
    try:
        if EPL_FPL.exists():
            age = time.time() - EPL_FPL.stat().st_mtime
            if age <= BOOTSTRAP_TTL_SEC:
                data = json_load(EPL_FPL)
                if isinstance(data, dict) and data.get("elements"):
                    return data
        # Нужно обновить
        r = requests.get(FPL_BOOTSTRAP_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("elements"):
            json_dump_atomic(EPL_FPL, data)
            return data
    except Exception as e:
        print(f"[EPL] Failed to fetch bootstrap-static: {e}")
    # fallback — что есть на диске
    return json_load(EPL_FPL) or {}

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
    data = json_load(EPL_FPL) or {}
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
    state.setdefault("rosters", {})
    state.setdefault("picks", [])
    state.setdefault("draft_order", [])
    state.setdefault("current_pick_index", 0)
    state.setdefault("draft_started_at", None)
    state.setdefault("lineups", {})
    limits = state.setdefault("limits", {})
    limits.setdefault("Max from club", 3)
    return state

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
    def add(pl: Dict[str, Any]):
        nm  = norm(pl.get("player_name") or pl.get("fullName"))
        club = (pl.get("clubName") or "").upper()
        if nm and club:
            ids = idx.get((nm, club))
            if ids: picked.update(ids)
    for arr in (state.get("rosters") or {}).values():
        if isinstance(arr, list):
            for pl in arr:
                if isinstance(pl, dict): add(pl)
    for row in (state.get("picks") or []):
        pl = (row or {}).get("player") or {}
        if isinstance(pl, dict): add(pl)
    return picked

def annotate_can_pick(players: List[Dict[str, Any]], state: Dict[str, Any], current_user: Optional[str]) -> None:
    if not current_user:
        for p in players: p["canPick"] = False
        return
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
        can_pos = pos in slots and pos_counts.get(pos, 0) < slots[pos]
        can_club = club_counts.get(club, 0) < max_from_club if club else True
        p["canPick"] = bool(can_pos and can_club)

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
