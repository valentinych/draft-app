"""
Transfer Routes - Unified transfer system endpoints for all draft types
"""

from flask import Blueprint, request, session, redirect, url_for, flash, abort, jsonify, render_template
from typing import Dict, Any, Optional
from .transfer_system import create_transfer_system
from .auth import login_required

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
@login_required
def execute_transfer(draft_type: str):
    """Execute a player transfer"""
    current_user = session.get("user_name")
    if not current_user:
        abort(401)
    
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        current_gw = get_current_gw(draft_type)
        
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
        
        # Validate transfer
        is_valid, error_msg = transfer_system.validate_transfer(
            state, current_user, out_player_id, in_player_data
        )
        
        if not is_valid:
            flash(f"Трансфер отклонен: {error_msg}", "danger")
            return redirect(request.referrer or url_for("home.index"))
        
        # Execute transfer
        updated_state = transfer_system.execute_transfer(
            state, current_user, out_player_id, in_player_data, current_gw
        )
        
        transfer_system.save_state(updated_state)
        
        flash("Трансфер успешно выполнен!", "success")
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        flash(f"Ошибка при выполнении трансфера: {str(e)}", "danger")
        return redirect(request.referrer or url_for("home.index"))


@bp.route("/<draft_type>/pick-transfer-player", methods=["POST"]) 
@login_required
def pick_transfer_player(draft_type: str):
    """Pick a transfer-out player for team"""
    current_user = session.get("user_name")
    if not current_user:
        abort(401)
    
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        current_gw = get_current_gw(draft_type)
        
        player_id = request.form.get("player_id", type=int)
        if not player_id:
            flash("Некорректный ID игрока", "danger")
            return redirect(request.referrer or url_for("home.index"))
        
        updated_state = transfer_system.pick_transfer_player(
            state, current_user, player_id, current_gw
        )
        
        transfer_system.save_state(updated_state)
        
        flash("Игрок успешно добавлен в состав!", "success")
        return redirect(request.referrer or url_for("home.index"))
        
    except Exception as e:
        flash(f"Ошибка при добавлении игрока: {str(e)}", "danger")
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
        
        return render_template(
            "transfer_history.html",
            draft_type=draft_type,
            history=history,
            users=users,
            selected_manager=manager_filter
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
