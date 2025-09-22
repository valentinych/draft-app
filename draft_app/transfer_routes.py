"""
Transfer Routes - Unified transfer system endpoints for all draft types
"""

from flask import Blueprint, request, session, redirect, url_for, flash, abort, jsonify, render_template
from typing import Dict, Any, Optional
from .transfer_system import create_transfer_system

bp = Blueprint("transfers", __name__, url_prefix="/transfers")


def get_current_gw(draft_type: str) -> int:
    """Get current gameweek for draft type - implement based on draft logic"""
    # This should be implemented based on each draft's current GW logic
    # For now, return a default value
    return 1


def get_draft_users(draft_type: str) -> list:
    """Get list of users for specific draft type"""
    if draft_type.upper() == "UCL":
        from .config import UCL_USERS
        return UCL_USERS
    elif draft_type.upper() == "EPL":
        from .config import EPL_USERS  
        return EPL_USERS
    elif draft_type.upper() == "TOP4":
        # Import TOP4 users from appropriate config
        return ["Ксана", "Саша", "Максим", "Андрей", "Сергей", "Тёма", "Женя", "Руслан"]
    return []


@bp.route("/<draft_type>/execute", methods=["POST"])
def execute_transfer(draft_type: str):
    """Execute a player transfer"""
    current_user = session.get("user_name")
    if not current_user:
        if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        abort(401)
    
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        current_gw = get_current_gw(draft_type)
        
        # Check if transfer window is active
        if not transfer_system.is_transfer_window_active(state):
            error_msg = "Трансферное окно не активно"
            if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
                return jsonify({"success": False, "error": error_msg})
            flash(error_msg, "warning")
            return redirect(request.referrer or url_for("home.index"))
        
        # Check if it's current user's turn
        current_manager = transfer_system.get_current_transfer_manager(state)
        if current_manager != current_user:
            if current_manager:
                error_msg = f"Сейчас ход менеджера {current_manager}"
                flash_type = "warning"
            else:
                error_msg = "Трансферное окно завершено"
                flash_type = "info"
            
            if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
                return jsonify({"success": False, "error": error_msg})
            flash(error_msg, flash_type)
            return redirect(request.referrer or url_for("home.index"))
        
        # Get transfer parameters
        out_player_id = request.form.get("out_player_id", type=int)
        in_player_data = {
            "playerId": request.form.get("in_player_id", type=int),
            "fullName": request.form.get("in_player_name", ""),
            "clubName": request.form.get("in_player_club", ""),
            "position": request.form.get("in_player_position", ""),
            "price": request.form.get("in_player_price", type=float, default=0.0)
        }
        
        if not out_player_id or not in_player_data["playerId"]:
            flash("Некорректные данные трансфера", "danger")
            return redirect(request.referrer or url_for("home.index"))
        
        # Validate transfer (skip window check since we already checked)
        is_valid, error_msg = transfer_system.validate_transfer(
            state, current_user, out_player_id, in_player_data, check_window=False
        )
        
        if not is_valid:
            error_response = f"Трансфер отклонен: {error_msg}"
            if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
                return jsonify({"success": False, "error": error_response})
            flash(error_response, "danger")
            return redirect(request.referrer or url_for("home.index"))
        
        # Execute transfer
        updated_state = transfer_system.execute_transfer(
            state, current_user, out_player_id, in_player_data, current_gw
        )
        
        transfer_system.save_state(updated_state)
        
        success_msg = "Трансфер успешно выполнен!"
        if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
            return jsonify({"success": True, "message": success_msg})
        
        flash(success_msg, "success")
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        error_msg = f"Ошибка при выполнении трансфера: {str(e)}"
        if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
            return jsonify({"success": False, "error": error_msg})
        flash(error_msg, "danger")
        return redirect(request.referrer or url_for("home.index"))


@bp.route("/<draft_type>/pick-transfer-player", methods=["POST"]) 
def pick_transfer_player(draft_type: str):
    """Pick a transfer-out player for team"""
    current_user = session.get("user_name")
    if not current_user:
        if request.content_type == 'application/json' or request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        abort(401)
    
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        current_gw = get_current_gw(draft_type)
        
        player_id = request.form.get("player_id", type=int)
        if not player_id:
            error_msg = "Некорректный ID игрока"
            if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
                return jsonify({"success": False, "error": error_msg})
            flash(error_msg, "danger")
            return redirect(request.referrer or url_for("home.index"))
        
        # Check if it's user's turn
        current_manager = transfer_system.get_current_transfer_manager(state)
        if current_manager != current_user:
            error_msg = f"Сейчас ход менеджера {current_manager}" if current_manager else "Трансферное окно неактивно"
            if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
                return jsonify({"success": False, "error": error_msg})
            flash(error_msg, "danger")
            return redirect(request.referrer or url_for("home.index"))
        
        updated_state = transfer_system.pick_transfer_player(
            state, current_user, player_id, current_gw
        )
        
        transfer_system.save_state(updated_state)
        
        success_msg = "Игрок отправлен в transfer out пул!"
        if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
            return jsonify({"success": True, "message": success_msg})
        
        flash(success_msg, "success")
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        error_msg = f"Ошибка при отправке игрока: {str(e)}"
        if request.headers.get('Content-Type') == 'application/x-www-form-urlencoded':
            return jsonify({"success": False, "error": error_msg})
        flash(error_msg, "danger")
        return redirect(request.referrer or url_for("home.index"))


@bp.route("/<draft_type>/available-players")
def available_players(draft_type: str):
    """Get available transfer players as JSON"""
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        available = transfer_system.get_available_transfer_players(state)
        
        return jsonify({
            "success": True,
            "players": available,
            "count": len(available)
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route("/<draft_type>/history")
def transfer_history(draft_type: str):
    """View transfer history"""
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        manager_filter = request.args.get("manager")
        history = transfer_system.get_transfer_history(state, manager_filter)
        
        users = get_draft_users(draft_type)
        
        # Get transfer window info
        is_window_active = transfer_system.is_transfer_window_active(state)
        active_window = transfer_system.get_active_transfer_window(state)
        current_manager = transfer_system.get_current_transfer_manager(state)
        current_user = session.get("user_name")
        
        # Get user's current roster for transfer out selection
        user_roster = []
        if current_user and current_manager == current_user and is_window_active:
            rosters = state.get("rosters", {})
            user_roster = rosters.get(current_user, [])
        
        return render_template(
            "transfer_history.html",
            draft_type=draft_type,
            history=history,
            users=users,
            selected_manager=manager_filter,
            is_window_active=is_window_active,
            active_window=active_window,
            current_manager=current_manager,
            current_user=current_user,
            user_roster=user_roster
        )
        
    except Exception as e:
        flash(f"Ошибка при загрузке истории: {str(e)}", "danger")
        return redirect(url_for("home.index"))


@bp.route("/<draft_type>/normalize", methods=["POST"])
def normalize_players(draft_type: str):
    """Normalize all players to have transfer tracking - Admin only"""
    if not session.get("godmode"):
        abort(403)
    
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        current_gw = get_current_gw(draft_type)
        
        updated_state = transfer_system.normalize_all_players(state, current_gw)
        transfer_system.save_state(updated_state)
        
        flash(f"Игроки {draft_type} нормализованы для системы трансферов", "success")
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        flash(f"Ошибка нормализации: {str(e)}", "danger")
        return redirect(request.referrer or url_for("home.index"))


@bp.route("/<draft_type>/validate", methods=["POST"])
def validate_transfer_ajax(draft_type: str):
    """Validate transfer via AJAX"""
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        current_user = session.get("user_name")
        if not current_user:
            return jsonify({"success": False, "error": "Not authenticated"}), 401
        
        out_player_id = request.json.get("out_player_id", type=int)
        in_player_data = request.json.get("in_player", {})
        
        is_valid, error_msg = transfer_system.validate_transfer(
            state, current_user, out_player_id, in_player_data
        )
        
        return jsonify({
            "success": True,
            "valid": is_valid,
            "message": error_msg
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route("/<draft_type>/window-status")
def transfer_window_status(draft_type: str):
    """Get transfer window status as JSON"""
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        is_active = transfer_system.is_transfer_window_active(state)
        active_window = transfer_system.get_active_transfer_window(state)
        current_manager = transfer_system.get_current_transfer_manager(state)
        schedule = transfer_system.get_transfer_schedule()
        
        return jsonify({
            "success": True,
            "window_active": is_active,
            "current_manager": current_manager,
            "window_info": active_window,
            "schedule": schedule
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route("/<draft_type>/start-window", methods=["POST"])
def start_transfer_window(draft_type: str):
    """Start transfer window - Admin only"""
    if not session.get("godmode"):
        abort(403)
    
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        gw = request.form.get("gw", type=int)
        if not gw:
            flash("Необходимо указать GW", "danger")
            return redirect(request.referrer or url_for("home.index"))
        
        # Get managers order (reverse standings for fair order)
        users = get_draft_users(draft_type)
        managers_order = request.form.getlist("managers_order") or users[::-1]  # Reverse order
        
        success = transfer_system.start_transfer_window(state, gw, managers_order)
        
        if success:
            transfer_system.save_state(state)
            flash(f"Трансферное окно для GW{gw} открыто", "success")
        else:
            flash("Не удалось открыть трансферное окно", "danger")
        
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        flash(f"Ошибка при открытии трансферного окна: {str(e)}", "danger")
        return redirect(request.referrer or url_for("home.index"))


@bp.route("/<draft_type>/close-window", methods=["POST"])
def close_transfer_window(draft_type: str):
    """Close transfer window - Admin only"""
    if not session.get("godmode"):
        abort(403)
    
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        success = transfer_system.close_transfer_window(state)
        
        if success:
            transfer_system.save_state(state)
            flash("Трансферное окно закрыто", "success")
        else:
            flash("Трансферное окно уже закрыто", "info")
        
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        flash(f"Ошибка при закрытии трансферного окна: {str(e)}", "danger")
        return redirect(request.referrer or url_for("home.index"))


@bp.route("/<draft_type>/skip-turn", methods=["POST"])
def skip_transfer_turn(draft_type: str):
    """Skip current manager's turn"""
    current_user = session.get("user_name")
    if not current_user:
        abort(401)
    
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        # Check if it's current user's turn or if user is admin
        current_manager = transfer_system.get_current_transfer_manager(state)
        if current_manager != current_user and not session.get("godmode"):
            flash("Это не ваш ход", "danger")
            return redirect(request.referrer or url_for("home.index"))
        
        success = transfer_system.advance_transfer_turn(state)
        
        if success:
            transfer_system.save_state(state)
            flash("Ход пропущен", "info")
        else:
            flash("Трансферное окно не активно", "warning")
        
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        flash(f"Ошибка при пропуске хода: {str(e)}", "danger")
        return redirect(request.referrer or url_for("home.index"))


# Helper function to get player active gameweeks - can be used by scoring logic
def get_player_scoring_gws(draft_type: str, player_id: int) -> list:
    """Get gameweeks when player should count for scoring"""
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        # Find player in rosters
        rosters = state.get("rosters", {})
        for manager, roster in rosters.items():
            for player in roster:
                if int(player.get("playerId", 0)) == player_id:
                    return transfer_system.get_player_active_gws(player)
        
        return []
        
    except Exception:
        return []
