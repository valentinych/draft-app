from __future__ import annotations
import json, os, tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from flask import Blueprint, render_template, request, session, url_for, redirect, abort, flash

bp = Blueprint("epl", __name__)

# --- файлы данных ---
BASE_DIR = Path(__file__).resolve().parent.parent
EPL_STATE = BASE_DIR / "draft_state_epl.json"
EPL_PLAYERS = BASE_DIR / "players_fpl_bootstrap.json"  # FPL bootstrap

# --- константы позиций / слоты для блока "Составы по менеджерам" ---
POS_CANON = {
    "Goalkeeper": "GK", "GK": "GK",
    "Defender": "DEF", "DEF": "DEF",
    "Midfielder": "MID", "MID": "MID",
    "Forward": "FWD", "FWD": "FWD",
}
DEFAULT_SLOTS = {"GK": 3, "DEF": 7, "MID": 8, "FWD": 4}  # при необходимости поправь под свои правила


# ----------------- I/O helpers -----------------
def _json_load(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None

def _json_dump_atomic(p: Path, data: Any):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="state_", suffix=".json", dir=str(p.parent))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ----------------- players -----------------
def _players_from_fpl(bootstrap: Any) -> List[Dict[str, Any]]:
    """
    Преобразует FPL bootstrap в унифицированный список игроков.
    Выход: [{playerId, shortName, fullName, clubName, position, price}, ...]
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(bootstrap, dict):
        return out

    elements = bootstrap.get("elements") or []
    teams = {t.get("id"): t.get("name") for t in (bootstrap.get("teams") or [])}
    pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

    for e in elements:
        pid = e.get("id")
        if pid is None:
            continue
        first = (e.get("first_name") or "").strip()
        second = (e.get("second_name") or "").strip()
        web = (e.get("web_name") or second or "").strip()
        full = f"{first} {second}".strip()
        out.append(
            {
                "playerId": int(pid),
                "shortName": web,           # краткое имя
                "fullName": full,           # полное имя
                "clubName": teams.get(e.get("team")) or str(e.get("team")),
                "position": pos_map.get(e.get("element_type")),
                # FPL хранит цену в десятых (55 => 5.5)
                "price": (e.get("now_cost") / 10.0) if isinstance(e.get("now_cost"), (int, float)) else None,
            }
        )
    return out

def _players_index(plist: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(p["playerId"]): p for p in plist}


# ----------------- state helpers -----------------
def _load_state() -> Dict[str, Any]:
    state = _json_load(EPL_STATE) or {}
    state.setdefault("rosters", {})         # составы по менеджерам
    state.setdefault("picks", [])           # список пиков
    state.setdefault("draft_order", [])     # порядок драфта
    state.setdefault("current_pick_index", 0)
    # время старта драфта
    state.setdefault("draft_started_at", None)
    return state

def _save_state(state: Dict[str, Any]):
    _json_dump_atomic(EPL_STATE, state)

def _picked_ids_from_state(state: Dict[str, Any]) -> set[str]:
    picked: set[str] = set()
    # из составов
    for arr in (state.get("rosters") or {}).values():
        if isinstance(arr, list):
            for pl in arr:
                pid = pl.get("playerId") or pl.get("id")
                if pid is not None:
                    picked.add(str(pid))
    # из пиков (на случай, если roster отстаёт)
    for row in (state.get("picks") or []):
        pl = (row or {}).get("player") or {}
        pid = pl.get("playerId") or pl.get("id")
        if pid is not None:
            picked.add(str(pid))
    return picked

def _who_is_on_the_clock(state: Dict[str, Any]) -> Optional[str]:
    try:
        order = state.get("draft_order") or []
        idx = int(state.get("current_pick_index", 0))
        if 0 <= idx < len(order):
            return order[idx]
        return None
    except Exception:
        return None

def _slots_from_state(state: Dict[str, Any]) -> Dict[str, int]:
    """Читаем желаемые слоты из state['limits']['Slots'] либо берём дефолт."""
    limits = state.get("limits") or {}
    slots = (limits.get("Slots") if isinstance(limits, dict) else None) or {}
    merged = DEFAULT_SLOTS.copy()
    if isinstance(slots, dict):
        for k, v in slots.items():
            if k in merged and isinstance(v, int) and v >= 0:
                merged[k] = v
    return merged


# ----------------- status context -----------------
def _build_status_context_epl() -> Dict[str, Any]:
    state = _load_state()

    bootstrap = _json_load(EPL_PLAYERS) or {}
    plist = _players_from_fpl(bootstrap)
    pidx = _players_index(plist)

    # limits
    limits = state.get("limits") or {"Max from club": 3, "Min GK": 1, "Min DEF": 3, "Min MID": 3, "Min FWD": 1}

    # picks (учитываем вложенное player и поле ts)
    picks: List[Dict[str, Any]] = []
    for row in state.get("picks", []):
        user = row.get("user")
        pl = row.get("player") or {}
        pid = str(pl.get("playerId") or pl.get("id") or "")
        meta = pidx.get(pid, {})
        pname = pl.get("player_name") or meta.get("shortName") or pl.get("fullName") or meta.get("fullName")
        picks.append({
            "round": row.get("round"),
            "user": user,
            "player_name": pname,
            "club": pl.get("clubName") or meta.get("clubName"),
            "pos": POS_CANON.get(pl.get("position")) or meta.get("position"),
            "ts": row.get("ts"),  # ISO-строка
        })

    # squads: rosters -> сгруппировать GK, DEF, MID, FWD и дополнить пустыми слотами
    rosters = state.get("rosters") or {}
    slots = _slots_from_state(state)  # {"GK":2,"DEF":5,"MID":5,"FWD":3}
    squads_grouped: Dict[str, Dict[str, List[Dict[str, Any] | None]]] = {}

    def canon_pos(x: Any) -> str:
        return POS_CANON.get(str(x)) or str(x)

    for manager, arr in rosters.items():
        g = {"GK": [], "DEF": [], "MID": [], "FWD": []}
        for pl in (arr or []):
            pid = str(pl.get("playerId") or pl.get("id") or "")
            meta = pidx.get(pid, {})
            pos = canon_pos(pl.get("position") or meta.get("position"))
            if pos not in g:
                continue
            g[pos].append({
                "fullName": pl.get("player_name") or pl.get("fullName") or meta.get("shortName") or meta.get("fullName"),
                "position": pos,
                "clubName": pl.get("clubName") or meta.get("clubName"),
            })
        # дополнить пустыми
        for pos in ("GK", "DEF", "MID", "FWD"):
            need = max(0, slots.get(pos, 0) - len(g[pos]))
            g[pos].extend([None] * need)
        squads_grouped[manager] = g

    ctx: Dict[str, Any] = {
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
    return ctx


# ----------------- routes -----------------
@bp.route("/epl", methods=["GET", "POST"])
def index():
    draft_title = "EPL Fantasy Draft"

    # загрузим игроков и стейт
    bootstrap = _json_load(EPL_PLAYERS) or {}
    players = _players_from_fpl(bootstrap)
    pidx = _players_index(players)

    state = _load_state()
    next_user = state.get("next_user") or _who_is_on_the_clock(state)
    next_round = state.get("next_round")
    draft_completed = bool(state.get("draft_completed", False))
    current_user = session.get("user_name")
    godmode = bool(session.get("godmode"))

    # --- обработка пика (POST) ---
    if request.method == "POST":
        if draft_completed:
            flash("Драфт завершён", "warning")
            return redirect(url_for("epl.index"))

        player_id = request.form.get("player_id")
        if not player_id or player_id not in pidx:
            flash("Некорректный игрок", "danger")
            return redirect(url_for("epl.index"))

        # защита: пик только в свой ход (кроме godmode)
        if not godmode and (not current_user or current_user != next_user):
            abort(403)

        picked_ids = _picked_ids_from_state(state)
        if str(player_id) in picked_ids:
            flash("Игрок уже выбран", "warning")
            return redirect(url_for("epl.index"))

        # зафиксируем время старта драфта при первом пике
        if not state.get("draft_started_at"):
            state["draft_started_at"] = datetime.now().isoformat(timespec="seconds")

        # запись пика с временем
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

        # добавим в состав менеджера
        roster = state["rosters"].setdefault(current_user, [])
        roster.append(pick_row["player"])

        # продвинем очередь
        try:
            state["current_pick_index"] = int(state.get("current_pick_index", 0)) + 1
            order = state.get("draft_order", [])
            if 0 <= state["current_pick_index"] < len(order):
                state["next_user"] = order[state["current_pick_index"]]
        except Exception:
            pass

        _save_state(state)
        return redirect(url_for("epl.index"))

    # --- GET: список игроков, фильтры, сортировка ---

    # скрыть уже выбранных
    picked_ids = _picked_ids_from_state(state)
    players = [p for p in players if str(p["playerId"]) not in picked_ids]

    # --- НОВОЕ: карта аббревиатур клубов -> полное имя (из FPL teams.short_name) ---
    teams = bootstrap.get("teams") or []
    abbr2name = {str(t.get("short_name")).upper(): t.get("name") for t in teams if t.get("short_name") and t.get("name")}

    # filters (поддержка аббревиатур, например ?club=ARS)
    club_filter = (request.args.get("club") or "").strip()
    pos_filter = (request.args.get("position") or "").strip()

    # опции для селектов
    clubs = sorted({p.get("clubName") for p in players if p.get("clubName")})
    positions = sorted({p.get("position") for p in players if p.get("position")})

    # нормализуем клуб: если передана аббревиатура (ARS/LIV/...), переведём в полное имя
    if club_filter and club_filter not in clubs:
        club_maybe = abbr2name.get(club_filter.upper())
        if club_maybe:
            club_filter = club_maybe
        else:
            # неизвестное значение фильтра — безопасно игнорируем
            club_filter = ""

    # применяем фильтры
    if club_filter:
        players = [p for p in players if (p.get("clubName") or "") == club_filter]
    if pos_filter:
        players = [p for p in players if (p.get("position") or "") == pos_filter]

    # сортировка по цене (?sort=price&dir=asc|desc)
    sort_field = request.args.get("sort")
    sort_dir = request.args.get("dir", "asc")
    reverse = sort_dir == "desc"
    if sort_field == "price":
        players.sort(key=lambda p: (p.get("price") is None, p.get("price")), reverse=reverse)

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
