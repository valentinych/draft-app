# draft_app/status.py
from __future__ import annotations
import json
from pathlib import Path
import tempfile
from typing import Dict, List, Any
from flask import Blueprint, render_template, url_for, abort

bp = Blueprint("status", __name__)

BASE_DIR = Path(__file__).resolve().parent.parent  # корень проекта
DATA_DIR = BASE_DIR / "data"                       # поменяйте если у вас другое

# Карта лиг -> файлы с состоянием/игроками
LEAGUE_FILES = {
    "epl": {
        "state": BASE_DIR / "draft_state_epl.json",
        "players": Path(tempfile.gettempdir()) / "players_fpl_bootstrap.json",  # кешируется в /tmp
    },
    "ucl": {
        "state": BASE_DIR / "draft_state_ucl.json",
        "players": BASE_DIR / "players_ucl.json",            # если есть; иначе закомментируйте
    },
    # добавьте другие лиги при необходимости
}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _players_index(players_any: Any) -> Dict[str, Dict[str, Any]]:
    """
    Строит индекс по playerId -> объект игрока.
    Поддерживает разные структуры (список словарей или dict).
    """
    idx: Dict[str, Dict[str, Any]] = {}
    if not players_any:
        return idx

    if isinstance(players_any, dict):
        # иногда бывает {'players':[...]}
        if "players" in players_any and isinstance(players_any["players"], list):
            src = players_any["players"]
        else:
            # уже dict id->player
            for k, v in players_any.items():
                if isinstance(v, dict):
                    idx[str(k)] = v
            return idx
    elif isinstance(players_any, list):
        src = players_any
    else:
        src = []

    for p in src:
        # поддержим разные ключи ID и имён
        pid = p.get("playerId") or p.get("id") or p.get("pid")
        if pid is not None:
            idx[str(pid)] = p
    return idx


def _build_context(state: Any, players_idx: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Формирует limits / picks / squads из максимально «свободной» структуры.
    Ожидаемые поля (если есть): state['picks'], state['limits'], state['teams'] / state['squads'].
    """
    ctx: Dict[str, Any] = {}

    # limits
    limits = None
    for key in ("limits", "rules", "draft_limits"):
        if state and isinstance(state, dict) and key in state:
            limits = state[key]
            break
    if not limits:
        # сделаем пример базовых лимитов, если в стейте их нет
        limits = {"Max from club": 3, "Min GK": 1, "Min DEF": 3, "Min MID": 3, "Min FWD": 1}
    ctx["limits"] = limits

    # picks — список {round, user, player_name, club, pos, ts}
    picks: List[Dict[str, Any]] = []
    raw_picks = None
    if state and isinstance(state, dict):
        for key in ("picks", "draft_picks", "picks_by_round"):
            if key in state:
                raw_picks = state[key]
                break

    if isinstance(raw_picks, list):
        for row in raw_picks:
            pid = str(row.get("playerId") or row.get("pid") or row.get("id") or "")
            pmeta = players_idx.get(pid, {})
            picks.append({
                "round": row.get("round"),
                "user": row.get("user") or row.get("manager") or row.get("drafter"),
                "player_name": row.get("player_name") or row.get("fullName") or pmeta.get("fullName") or pmeta.get("name"),
                "club": row.get("club") or row.get("clubName") or pmeta.get("clubName") or pmeta.get("team"),
                "pos": row.get("pos") or row.get("position") or pmeta.get("position"),
                "ts": row.get("ts") or row.get("timestamp"),
            })
    ctx["picks"] = picks

    # squads — dict manager -> list[player]
    squads = None
    if state and isinstance(state, dict):
        for key in ("squads", "teams", "squads_by_manager"):
            if key in state:
                squads = state[key]
                break

    squads_norm: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(squads, dict):
        for manager, arr in squads.items():
            lst = []
            if isinstance(arr, list):
                for x in arr:
                    if isinstance(x, dict) and ("playerId" in x or "id" in x):
                        pid = str(x.get("playerId") or x.get("id"))
                        meta = players_idx.get(pid, {})
                        lst.append({
                            "fullName": x.get("fullName") or x.get("player_name") or meta.get("fullName") or meta.get("name"),
                            "position": x.get("position") or meta.get("position"),
                            "clubName": x.get("clubName") or meta.get("clubName") or meta.get("team"),
                        })
                    else:
                        # бывает, что в массиве просто ID
                        pid = str(x)
                        meta = players_idx.get(pid, {})
                        if meta:
                            lst.append({
                                "fullName": meta.get("fullName") or meta.get("name"),
                                "position": meta.get("position"),
                                "clubName": meta.get("clubName") or meta.get("team"),
                            })
            squads_norm[manager] = lst
    elif not squads and picks:
        # если нет явных составов — построим по пикам
        for row in picks:
            manager = row.get("user") or "Unknown"
            squads_norm.setdefault(manager, [])
            squads_norm[manager].append({
                "fullName": row.get("player_name"),
                "position": row.get("pos"),
                "clubName": row.get("club"),
            })

    ctx["squads"] = squads_norm

    # статусы
    ctx["draft_completed"] = bool((state or {}).get("draft_completed", False))
    ctx["next_user"] = (state or {}).get("next_user")
    ctx["next_round"] = (state or {}).get("next_round")

    return ctx


def _load_for_league(league: str) -> Dict[str, Any]:
    files = LEAGUE_FILES.get(league)
    if not files:
        return {}

    state = _read_json(Path(files["state"])) if files.get("state") else None
    players_raw = _read_json(Path(files["players"])) if files.get("players") else None
    pidx = _players_index(players_raw)

    return _build_context(state or {}, pidx)


# Use a non-conflicting path so league-specific blueprints like
# UCL/EPL can own "/<league>/status" without collisions.
@bp.get("/status/<league>")
def status(league: str):
    league = league.lower()
    ctx = _load_for_league(league)
    if ctx is None:
        abort(404)
    ctx["title"] = f"{league.upper()} Fantasy Draft — Состояние драфта"
    # Куда вернуться к драфту:
    # подставьте ваш реальный роут, если отличается
    draft_route = f"{league}.index" if f"{league}.index" in bp.app.view_functions else "epl.index"
    ctx["draft_url"] = url_for(draft_route)

    return render_template("status.html", **ctx)
