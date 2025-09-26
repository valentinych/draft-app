from __future__ import annotations
from flask import Blueprint, render_template, request, session, url_for, redirect, abort, flash, jsonify
from datetime import datetime
from typing import Dict

from .top4_services import (
    load_players, players_index,
    load_state, save_state, who_is_on_clock,
    picked_ids_from_state, annotate_can_pick,
    build_status_context,
    wishlist_load, wishlist_save,
)
from .top4_schedule import build_schedule
from .mantra_store import mantra_store

# Blueprint for draft-related routes.  Renamed to avoid conflict with the
# separate ``top4`` blueprint used for statistics and lineups.
bp = Blueprint("top4draft", __name__)

@bp.route("/top4", methods=["GET", "POST"])
def index():
    draft_title = "Top-4 Fantasy Draft"
    
    # Check if we should use MantraFootball data
    use_mantra = request.args.get('use_mantra', '').lower() == 'true'
    current_user = session.get("user_name")
    
    if use_mantra:
        # Load players from MantraFootball
        mantra_players = mantra_store.get_players()
        if mantra_players:
            players = mantra_players
        else:
            # Fallback to original data if MantraFootball data not available
            players = load_players()
            flash("MantraFootball data not available. Using original data.", "info")
    else:
        players = load_players()
    
    pidx = players_index(players)
    state = load_state()
    next_user = state.get("next_user") or who_is_on_clock(state)
    draft_completed = bool(state.get("draft_completed", False))

    if request.method == "POST":
        if draft_completed:
            flash("Драфт завершён", "warning"); return redirect(url_for("top4draft.index"))
        if not current_user or current_user != next_user:
            abort(403)
        player_id = request.form.get("player_id")
        if not player_id or player_id not in pidx:
            flash("Некорректный игрок", "danger"); return redirect(url_for("top4draft.index"))
        picked = picked_ids_from_state(state)
        if str(player_id) in picked:
            flash("Игрок уже выбран", "warning"); return redirect(url_for("top4draft.index"))
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
                "league": meta.get("league"),
            },
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        state.setdefault("picks", []).append(pick_row)
        state.setdefault("rosters", {}).setdefault(current_user, []).append(pick_row["player"])
        state["current_pick_index"] = int(state.get("current_pick_index", 0)) + 1
        order = state.get("draft_order", [])
        if 0 <= state["current_pick_index"] < len(order):
            state["next_user"] = order[state["current_pick_index"]]
        else:
            state["next_user"] = None
            state["draft_completed"] = True
        save_state(state)
        return redirect(url_for("top4draft.index"))

    picked_ids = picked_ids_from_state(state)
    players = [p for p in players if str(p["playerId"]) not in picked_ids]

    league_filter = (request.args.get("league") or "").strip()
    club_filter = (request.args.get("club") or "").strip()
    pos_filter = (request.args.get("position") or "").strip()

    leagues = sorted({p.get("league") for p in players if p.get("league")})
    if league_filter:
        players = [p for p in players if p.get("league") == league_filter]
    clubs = sorted({p.get("clubName") for p in players if p.get("clubName")})
    if club_filter:
        players = [p for p in players if p.get("clubName") == club_filter]
    positions = sorted({p.get("position") for p in players if p.get("position")})
    if pos_filter:
        players = [p for p in players if p.get("position") == pos_filter]

    # Sorting
    sort_field = request.args.get("sort") or "price"
    sort_dir = request.args.get("dir") or "desc"
    reverse = sort_dir == "desc"
    if sort_field == "price":
        players.sort(key=lambda p: (p.get("price") is None, p.get("price")), reverse=reverse)
    elif sort_field == "popularity":
        players.sort(key=lambda p: (p.get("popularity") is None, p.get("popularity")), reverse=reverse)
    elif sort_field == "pts":
        players.sort(key=lambda p: (p.get("fp_last") is None, p.get("fp_last")), reverse=reverse)

    annotate_can_pick(players, state, current_user)

    # Check for transfer window status
    transfer_window_active = False
    current_transfer_manager = None
    current_transfer_phase = None
    
    try:
        from .transfer_system import create_transfer_system
        transfer_system = create_transfer_system("top4")
        
        # CRITICAL FIX: Use the same state loading method as API routes
        transfer_state = transfer_system.load_state()
        transfer_window_active = transfer_system.is_transfer_window_active(transfer_state)
        
        if transfer_window_active:
            current_transfer_manager = transfer_system.get_current_transfer_manager(transfer_state)
            current_transfer_phase = transfer_system.get_current_transfer_phase(transfer_state)
            
            # Filter players based on transfer phase
            if current_user == current_transfer_manager:
                if current_transfer_phase == "out":
                    # Show only current user's roster for transfer out
                    user_roster = transfer_state.get("rosters", {}).get(current_user, [])
                    user_player_ids = {str(p.get("playerId")) for p in user_roster}
                    players = [p for p in players if str(p["playerId"]) in user_player_ids]
                elif current_transfer_phase == "in":
                    # Show available transfer players for transfer in
                    available_players = transfer_system.get_available_transfer_players(transfer_state)
                    available_player_ids = {str(p.get("playerId")) for p in available_players}
                    players = [p for p in players if str(p["playerId"]) in available_player_ids]
    except Exception as e:
        print(f"Error checking transfer window: {e}")

    return render_template(
        "index.html",
        draft_title=draft_title,
        players=players,
        leagues=leagues,
        clubs=clubs,
        positions=positions,
        league_filter=league_filter,
        club_filter=club_filter,
        pos_filter=pos_filter,
        table_league="top4",
        current_user=current_user,
        next_user=next_user,
        next_round=state.get("next_round"),
        draft_completed=draft_completed,
        status_url=url_for("top4draft.status"),
        undo_url=url_for("top4draft.undo_last_pick"),
        transfer_window_active=transfer_window_active,
        current_transfer_manager=current_transfer_manager,
        current_transfer_phase=current_transfer_phase,
    )

@bp.get("/top4/status")
def status():
    ctx = build_status_context()
    ctx["draft_url"] = url_for("top4draft.index")
    return render_template("status.html", **ctx)


@bp.get("/top4/schedule")
def schedule_view():
    """Show Top-4 schedule with transfer windows"""
    data = build_schedule()
    
    # Check for transfer window status
    try:
        from .transfer_system import create_transfer_system
        state = load_state()
        transfer_system = create_transfer_system("top4")
        
        transfer_info = {
            'window_active': transfer_system.is_transfer_window_active(state),
            'current_manager': transfer_system.get_current_transfer_manager(state),
            'current_phase': transfer_system.get_current_transfer_phase(state),
            'active_window': transfer_system.get_active_transfer_window(state)
        }
        
        # Get transfer history
        history = transfer_system.get_transfer_history(state)
        
        # Group history by rounds
        rounds_completed = 0
        current_round_transfers = 0
        
        if transfer_info['active_window']:
            current_round = transfer_info['active_window'].get('current_round', 1)
            total_rounds = transfer_info['active_window'].get('total_rounds', 3)
            managers_order = transfer_info['active_window'].get('managers_order', [])
            current_manager_index = transfer_info['active_window'].get('current_manager_index', 0)
            
            # Count completed transfers in current round
            transfers_in_current_round = [t for t in history if t.get('action') == 'transfer_in']
            completed_in_current_round = len([t for t in transfers_in_current_round if t.get('gw') == 1])
            
            rounds_completed = current_round - 1
            current_round_transfers = completed_in_current_round % len(managers_order) if managers_order else 0
            
            transfer_info.update({
                'current_round': current_round,
                'total_rounds': total_rounds,
                'managers_order': managers_order,
                'current_manager_index': current_manager_index,
                'rounds_completed': rounds_completed,
                'current_round_transfers': current_round_transfers
            })
        
    except Exception as e:
        print(f"Error getting transfer info: {e}")
        transfer_info = {'window_active': False}
    
    return render_template("schedule.html", schedule=data, transfer_info=transfer_info)


@bp.post("/top4/undo")
def undo_last_pick():
    if not session.get("godmode"):
        abort(403)
    state = load_state()
    picks = state.get("picks") or []
    if not picks:
        flash("Нет пиков для отмены", "warning")
        return redirect(url_for("top4draft.index"))
    last = picks.pop()
    user = last.get("user")
    pl = (last.get("player") or {})
    pid = pl.get("playerId")
    roster = (state.get("rosters") or {}).get(user)
    if isinstance(roster, list) and pid is not None:
        for i, it in enumerate(roster):
            if isinstance(it, dict) and (it.get("playerId") == pid or it.get("id") == pid):
                roster.pop(i)
                break
    try:
        idx = int(state.get("current_pick_index", 0)) - 1
        if idx < 0:
            idx = 0
        state["current_pick_index"] = idx
        order = state.get("draft_order", [])
        state["next_user"] = order[idx] if 0 <= idx < len(order) else None
    except Exception:
        pass
    state["draft_completed"] = False
    save_state(state)
    flash("Последний пик отменён", "success")
    return redirect(url_for("top4draft.index"))

# ---- Wishlist API ----
def get_transfer_order_from_results() -> list[str]:
    """Get transfer order based on current Top-4 results (lowest total first)"""
    try:
        # First try to get results from production server
        import requests
        try:
            response = requests.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/results/data", timeout=10)
            if response.status_code == 200:
                production_data = response.json()
                lineups = production_data.get("lineups", {})
                
                # Calculate total points for each manager from production data
                manager_scores = []
                for manager, data in lineups.items():
                    if isinstance(data, dict):
                        total = data.get("total", 0)
                    else:
                        total = 0
                    manager_scores.append((manager, total))
                    print(f"Manager {manager}: {total} points (from production)")
                
                if manager_scores:
                    # Sort by total points ascending (worst first for transfer priority)
                    manager_scores.sort(key=lambda x: x[1])
                    transfer_order = [manager for manager, _ in manager_scores]
                    
                    print(f"Transfer order (worst to best): {transfer_order}")
                    return transfer_order
        except Exception as e:
            print(f"Could not fetch production data: {e}")
        
        # Fallback to local results calculation
        print("Falling back to local results calculation...")
        from .mantra_routes import _build_results
        state = load_state()
        results = _build_results(state)
        
        # results structure: {"lineups": {manager: {"players": [...], "total": int}}, "managers": [...]}
        lineups = results.get("lineups", {})
        
        # Calculate total points for each manager
        manager_scores = []
        for manager, data in lineups.items():
            if isinstance(data, dict):
                total = data.get("total", 0)
            else:
                total = 0
            manager_scores.append((manager, total))
            print(f"Manager {manager}: {total} points (from local)")
        
        if not manager_scores:
            print("No manager scores found, using default order")
            from .config import TOP4_USERS
            return TOP4_USERS
        
        # Sort by total points ascending (worst first for transfer priority)
        manager_scores.sort(key=lambda x: x[1])
        transfer_order = [manager for manager, _ in manager_scores]
        
        print(f"Transfer order (worst to best): {transfer_order}")
        return transfer_order
        
    except Exception as e:
        print(f"Error getting transfer order from results: {e}")
        import traceback
        traceback.print_exc()
        # Fallback to default order
        from .config import TOP4_USERS
        return TOP4_USERS


@bp.route("/top4/api/wishlist", methods=["GET", "PATCH", "POST"])
def wishlist_api():
    user = session.get("user_name")
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if request.method == "GET":
        ids = wishlist_load(user)
        return jsonify({"manager": user, "ids": ids})
    if request.method == "PATCH":
        payload = request.get_json(silent=True) or {}
        to_add = payload.get("add") or []
        to_rm = payload.get("remove") or []
        try:
            cur = set(wishlist_load(user))
            cur.update(int(x) for x in to_add)
            cur.difference_update(int(x) for x in to_rm)
            ids = sorted(cur)
            wishlist_save(user, ids)
            return jsonify({"ok": True, "ids": ids})
        except Exception as e:
            return jsonify({"error": "bad payload", "details": str(e)}), 400
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        ids = payload.get("ids")
        if not isinstance(ids, list):
            return jsonify({"error": "ids must be list"}), 400
        try:
            wishlist_save(user, [int(x) for x in ids])
            return jsonify({"ok": True, "ids": wishlist_load(user)})
        except Exception as e:
            return jsonify({"error": "cannot save", "details": str(e)}), 400
    return jsonify({"error": "method not allowed"}), 405


def create_backup(state, reason="manual"):
    """Create a backup of the current state"""
    try:
        from datetime import datetime
        import json
        
        # Create backup with timestamp
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_key = f"backups/top4/draft_state_top4_{timestamp}_{reason}.json"
        
        from .top4_services import _s3_enabled, _s3_bucket, _s3_put_json
        
        if _s3_enabled():
            bucket = _s3_bucket()
            if bucket and _s3_put_json(bucket, backup_key, state):
                print(f"Created backup: s3://{bucket}/{backup_key}")
                return True
        
        # Local backup as fallback
        from pathlib import Path
        backup_dir = Path(__file__).resolve().parent.parent / "data" / "backups" / "top4"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        backup_file = backup_dir / f"draft_state_top4_{timestamp}_{reason}.json"
        backup_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Created local backup: {backup_file}")
        return True
        
    except Exception as e:
        print(f"Failed to create backup: {e}")
        return False


@bp.route("/top4/open_transfer_window", methods=["POST"])
def open_transfer_window():
    """Open Top-4 transfer window - godmode only"""
    if not session.get("godmode"):
        abort(403)
    
    try:
        from .transfer_system import init_transfers_for_league
        
        # Create backup before opening transfer window
        state = load_state()
        create_backup(state, "before_transfer_window")
        
        # Get transfer order based on current results
        transfer_order = get_transfer_order_from_results()
        
        print(f"Opening Top-4 transfer window with order: {transfer_order}")
        
        # Initialize transfer window with 3 rounds (not snake)
        success = init_transfers_for_league(
            draft_type="top4",
            participants=transfer_order,
            transfers_per_manager=3,  # 3 rounds of transfers
            position_limits={"GK": 2, "DEF": 5, "MID": 5, "FWD": 3},
            max_from_club=1
        )
        
        if success:
            flash("Трансферное окно Top-4 открыто! Очередность: " + " → ".join(transfer_order), "success")
        else:
            flash("Ошибка при открытии трансферного окна", "error")
            
    except Exception as e:
        print(f"Error opening Top-4 transfer window: {e}")
        flash(f"Ошибка при открытии трансферного окна: {str(e)}", "error")
    
    return redirect(request.referrer or url_for("top4draft.index"))
