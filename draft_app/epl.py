from __future__ import annotations
import json, os, tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime
from flask import Blueprint, render_template, request, session, url_for, redirect, abort, flash, jsonify

bp = Blueprint("epl", __name__)

# ----------------- конфиг путей -----------------
BASE_DIR = Path(__file__).resolve().parent.parent
EPL_STATE = BASE_DIR / "draft_state_epl.json"
EPL_FPL   = BASE_DIR / "players_fpl_bootstrap.json"  # источник игроков
WISHLIST_DIR = BASE_DIR / "data" / "wishlist" / "epl"

# ----------------- константы -----------------
POS_CANON = {
    "Goalkeeper": "GK", "GK": "GK",
    "Defender": "DEF", "DEF": "DEF",
    "Midfielder": "MID", "MID": "MID",
    "Forward": "FWD", "FWD": "FWD",
}
# размер состава
DEFAULT_SLOTS = {"GK": 3, "DEF": 7, "MID": 8, "FWD": 4}

# ----------------- I/O helpers -----------------
def _json_load(p: Path) -> Any:
    try:
        if p.exists():
            t = p.read_text(encoding="utf-8")
            return json.loads(t)
        return None
    except Exception:
        return None

def _json_dump_atomic(p: Path, data: Any):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="state_", suffix=".json", dir=str(p.parent))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)

# ----------------- players: FPL bootstrap -----------------
def _players_from_fpl(bootstrap: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(bootstrap, dict):
        return out
    elements = bootstrap.get("elements") or []
    teams = {t.get("id"): t.get("name") for t in (bootstrap.get("teams") or [])}
    short = {t.get("id"): (t.get("short_name") or "").upper() for t in (bootstrap.get("teams") or [])}
    pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    for e in elements:
        pid = e.get("id")
        if pid is None:
            continue
        first = (e.get("first_name") or "").strip()
        second = (e.get("second_name") or "").strip()
        web = (e.get("web_name") or second or "").strip()
        full = f"{first} {second}".strip()
        club_full = teams.get(e.get("team")) or str(e.get("team"))
        club_abbr = short.get(e.get("team")) or (club_full or "").upper()
        out.append({
            "playerId": int(pid),
            "shortName": web,
            "fullName": full,
            "clubName": club_abbr,
            "clubFull": club_full,
            "position": pos_map.get(e.get("element_type")),
            "price": (e.get("now_cost") / 10.0) if isinstance(e.get("now_cost"), (int, float)) else None,
        })
    return out

def _players_index(plist: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(p["playerId"]): p for p in plist}

def _nameclub_index(plist: List[Dict[str, Any]]) -> Dict[Tuple[str,str], Set[str]]:
    def _norm(s: Optional[str]) -> str:
        if not s: return ""
        return " ".join(str(s).replace(".", " ").split()).lower()
    idx: Dict[Tuple[str,str], Set[str]] = {}
    for p in plist:
        pid = str(p["playerId"])
        club = (p.get("clubName") or "").upper()
        for nm in (p.get("shortName"), p.get("fullName")):
            key = (_norm(nm), club)
            if not key[0] or not club: continue
            idx.setdefault(key, set()).add(pid)
    return idx

# ----------------- state helpers -----------------
def _load_state() -> Dict[str, Any]:
    state = _json_load(EPL_STATE) or {}
    state.setdefault("rosters", {})
    state.setdefault("picks", [])
    state.setdefault("draft_order", [])
    state.setdefault("current_pick_index", 0)
    state.setdefault("draft_started_at", None)
    limits = state.setdefault("limits", {})
    limits.setdefault("Max from club", 3)
    return state

def _save_state(state: Dict[str, Any]):
    _json_dump_atomic(EPL_STATE, state)

def _who_is_on_the_clock(state: Dict[str, Any]) -> Optional[str]:
    try:
        order = state.get("draft_order") or []
        idx = int(state.get("current_pick_index", 0))
        return order[idx] if 0 <= idx < len(order) else None
    except Exception:
        return None

def _slots_from_state(state: Dict[str, Any]) -> Dict[str, int]:
    limits = state.get("limits") or {}
    slots = (limits.get("Slots") if isinstance(limits, dict) else None) or {}
    merged = DEFAULT_SLOTS.copy()
    if isinstance(slots, dict):
        for k, v in slots.items():
            if k in merged and isinstance(v, int) and v >= 0:
                merged[k] = v
    return merged

def _picked_fpl_ids_from_state(
    state: Dict[str, Any],
    nameclub_idx: Dict[Tuple[str,str], Set[str]]
) -> Set[str]:
    def _norm(s: Optional[str]) -> str:
        if not s: return ""
        return " ".join(str(s).replace(".", " ").split()).lower()
    picked: Set[str] = set()
    def add_by_player(pl: Dict[str, Any]):
        nm  = _norm(pl.get("player_name") or pl.get("fullName"))
        club = (pl.get("clubName") or "").upper()
        if nm and club:
            ids = nameclub_idx.get((nm, club))
            if ids: picked.update(ids)
    for arr in (state.get("rosters") or {}).values():
        if isinstance(arr, list):
            for pl in arr:
                if isinstance(pl, dict): add_by_player(pl)
    for row in (state.get("picks") or []):
        pl = (row or {}).get("player") or {}
        if isinstance(pl, dict): add_by_player(pl)
    return picked

# ----------------- статусный контекст -----------------
def _build_status_context_epl() -> Dict[str, Any]:
    state = _load_state()
    bootstrap = _json_load(EPL_FPL) or {}
    plist = _players_from_fpl(bootstrap)

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

    slots = _slots_from_state(state)
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
        "draft_url": url_for("epl.index"),
        "limits": limits,
        "picks": picks,
        "squads_grouped": squads_grouped,
        "draft_completed": bool(state.get("draft_completed", False)),
        "next_user": state.get("next_user") or _who_is_on_the_clock(state),
        "next_round": state.get("next_round"),
        "draft_started_at": state.get("draft_started_at"),
    }

# ----------------- расчёт флага canPick -----------------
def _annotate_can_pick(players: List[Dict[str, Any]], state: Dict[str, Any], current_user: Optional[str]) -> None:
    if not current_user:
        for p in players: p["canPick"] = False
        return
    draft_completed = bool(state.get("draft_completed", False))
    on_clock = (state.get("next_user") or _who_is_on_the_clock(state)) == current_user
    if draft_completed or not on_clock:
        for p in players: p["canPick"] = False
        return
    roster = (state.get("rosters") or {}).get(current_user, []) or []
    slots = _slots_from_state(state)
    max_from_club = (state.get("limits") or {}).get("Max from club", 3)
    pos_counts: Dict[str, int] = {"GK":0, "DEF":0, "MID":0, "FWD":0}
    club_counts: Dict[str, int] = {}
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

# ----------------- wishlist storage -----------------
def _wishlist_path(manager: str) -> Path:
    safe = manager.replace("/", "_")
    return WISHLIST_DIR / f"{safe}.json"

def _wishlist_load(manager: str) -> List[int]:
    p = _wishlist_path(manager)
    try:
        data = _json_load(p)
        if isinstance(data, list):
            return [int(x) for x in data]
    except Exception:
        pass
    return []

def _wishlist_save(manager: str, ids: List[int]) -> None:
    WISHLIST_DIR.mkdir(parents=True, exist_ok=True)
    _json_dump_atomic(_wishlist_path(manager), [int(x) for x in ids])

# ----------------- маршруты: страница драфта -----------------
@bp.route("/epl", methods=["GET", "POST"])
def index():
    draft_title = "EPL Fantasy Draft"

    # Загрузка игроков
    bootstrap = _json_load(EPL_FPL) or {}
    players = _players_from_fpl(bootstrap)
    pidx = _players_index(players)
    nameclub_idx = _nameclub_index(players)

    # Загрузка state
    state = _load_state()
    next_user = state.get("next_user") or _who_is_on_the_clock(state)
    next_round = state.get("next_round")
    draft_completed = bool(state.get("draft_completed", False))
    current_user = session.get("user_name")
    godmode = bool(session.get("godmode"))

    # POST: пик игрока
    if request.method == "POST":
        if draft_completed:
            flash("Драфт завершён", "warning"); return redirect(url_for("epl.index"))
        player_id = request.form.get("player_id")
        if not player_id or player_id not in pidx:
            flash("Некорректный игрок", "danger"); return redirect(url_for("epl.index"))
        if not godmode and (not current_user or current_user != next_user):
            abort(403)
        picked_fpl_ids = _picked_fpl_ids_from_state(state, nameclub_idx)
        if str(player_id) in picked_fpl_ids:
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
        _save_state(state)
        return redirect(url_for("epl.index"))

    # GET: скрываем уже выбранных
    picked_fpl_ids = _picked_fpl_ids_from_state(state, nameclub_idx)
    players = [p for p in players if str(p["playerId"]) not in picked_fpl_ids]

    # Фильтры
    club_filter = (request.args.get("club") or "").strip()
    pos_filter  = (request.args.get("position") or "").strip()
    clubs = sorted({p.get("clubName") for p in players if p.get("clubName")})
    positions = sorted({p.get("position") for p in players if p.get("position")})

    # поддержим полные имена клубов: Arsenal -> ARS
    teams = bootstrap.get("teams") or []
    abbr2name = {str(t.get("short_name")).upper(): t.get("name") for t in teams if t.get("short_name") and t.get("name")}
    name2abbr = {v.upper(): k for k, v in abbr2name.items()}
    if club_filter and club_filter not in clubs:
        club_filter = name2abbr.get(club_filter.upper(), "")
    if club_filter:
        club_key = club_filter.upper()
        players = [p for p in players if (p.get("clubName") or "").upper() == club_key]
    if pos_filter:
        players = [p for p in players if (p.get("position") or "") == pos_filter]

    # Сортировка по цене (?sort=price&dir=asc|desc)
    sort_field = request.args.get("sort") or "price"
    sort_dir = request.args.get("dir") or "desc"
    reverse = sort_dir == "desc"

    if sort_field == "price":
        players.sort(key=lambda p: (p.get("price") is None, p.get("price")), reverse=reverse)


    # Рассчитать canPick для фильтра “Can Pick”
    _annotate_can_pick(players, state, current_user)

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
    ctx = _build_status_context_epl()
    return render_template("status.html", **ctx)

@bp.post("/epl/undo")
def undo_last_pick():
    # Только для godmode
    if not session.get("godmode"):
        abort(403)

    state = _load_state()
    picks = state.get("picks") or []
    if not picks:
        flash("Нет пиков для отмены", "warning")
        return redirect(url_for("epl.index"))

    last = picks.pop()  # снять последний пик
    user = last.get("user")
    pl = (last.get("player") or {})
    pid = pl.get("playerId")

    # убрать игрока из состава соответствующего менеджера
    roster = (state.get("rosters") or {}).get(user)
    if isinstance(roster, list) and pid is not None:
        for i, it in enumerate(roster):
            if (isinstance(it, dict) and (it.get("playerId") == pid or it.get("id") == pid)):
                roster.pop(i)
                break

    # откатить очередь
    try:
        idx = int(state.get("current_pick_index", 0)) - 1
        if idx < 0:
            idx = 0
        state["current_pick_index"] = idx
        order = state.get("draft_order", [])
        if 0 <= idx < len(order):
            state["next_user"] = order[idx]
        else:
            state["next_user"] = None
    except Exception:
        pass

    # на всякий случай снимаем флаг завершения
    state["draft_completed"] = False

    _save_state(state)
    flash("Последний пик отменён", "success")
    return redirect(url_for("epl.index"))


# ----------------- API: wishlist -----------------
@bp.route("/epl/api/wishlist", methods=["GET", "PATCH", "POST"])
def wishlist_api():
    """
    GET    -> вернуть текущий список id для session user
    PATCH  -> {"add":[...], "remove":[...]} (оба поля опциональны)
    POST   -> {"ids":[...]} полная замена
    """
    user = session.get("user_name")
    if not user:
        return jsonify({"error": "not authenticated"}), 401

    if request.method == "GET":
        ids = _wishlist_load(user)
        return jsonify({"manager": user, "ids": ids})

    # Модификация — только для своего списка
    if request.method == "PATCH":
        payload = request.get_json(silent=True) or {}
        to_add = payload.get("add") or []
        to_rm  = payload.get("remove") or []
        try:
            cur = set(_wishlist_load(user))
            cur.update(int(x) for x in to_add)
            cur.difference_update(int(x) for x in to_rm)
            ids = sorted(cur)
            _wishlist_save(user, ids)
            return jsonify({"ok": True, "ids": ids})
        except Exception as e:
            return jsonify({"error": "bad payload", "details": str(e)}), 400

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        ids = payload.get("ids")
        if not isinstance(ids, list):
            return jsonify({"error": "ids must be list"}), 400
        try:
            _wishlist_save(user, [int(x) for x in ids])
            return jsonify({"ok": True, "ids": _wishlist_load(user)})
        except Exception as e:
            return jsonify({"error": "cannot save", "details": str(e)}), 400

    return jsonify({"error": "method not allowed"}), 405
