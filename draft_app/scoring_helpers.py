"""
Scoring Helpers - Functions to integrate transfer system with scoring logic
"""

from typing import Dict, Any, List, Optional
from .transfer_system import create_transfer_system


def should_player_score_for_gw(draft_type: str, player_id: int, gw: int) -> bool:
    """
    Check if player should score points for specific GW based on transfer history
    
    Args:
        draft_type: Type of draft ('UCL', 'EPL', 'TOP4')
        player_id: Player ID to check
        gw: Gameweek number
        
    Returns:
        True if player should score for this GW, False otherwise
    """
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        # Find player in any roster
        rosters = state.get("rosters", {})
        for manager, roster in rosters.items():
            for player in roster:
                if int(player.get("playerId", 0)) == player_id:
                    active_gws = transfer_system.get_player_active_gws(player)
                    return gw in active_gws
        
        return True  # Default behavior if player not found in transfer system
        
    except Exception:
        return True  # Default behavior on error


def get_player_manager_for_gw(draft_type: str, player_id: int, gw: int) -> Optional[str]:
    """
    Get which manager owns the player for specific GW
    
    Args:
        draft_type: Type of draft ('UCL', 'EPL', 'TOP4')
        player_id: Player ID to check
        gw: Gameweek number
        
    Returns:
        Manager name if player is active for this manager in this GW, None otherwise
    """
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        # Find player in any roster
        rosters = state.get("rosters", {})
        for manager, roster in rosters.items():
            for player in roster:
                if int(player.get("playerId", 0)) == player_id:
                    active_gws = transfer_system.get_player_active_gws(player)
                    if gw in active_gws:
                        return manager
        
        return None
        
    except Exception:
        return None


def filter_roster_for_gw(draft_type: str, roster: List[Dict[str, Any]], gw: int) -> List[Dict[str, Any]]:
    """
    Filter roster to only include players active for specific GW
    
    Args:
        draft_type: Type of draft ('UCL', 'EPL', 'TOP4')
        roster: List of player dictionaries
        gw: Gameweek number
        
    Returns:
        Filtered roster with only active players for this GW
    """
    try:
        transfer_system = create_transfer_system(draft_type)
        
        active_roster = []
        for player in roster:
            # Ensure player has transfer tracking fields
            normalized_player = transfer_system.normalize_player_data(player, gw)
            active_gws = transfer_system.get_player_active_gws(normalized_player)
            
            if gw in active_gws:
                active_roster.append(normalized_player)
        
        return active_roster
        
    except Exception:
        return roster  # Return original roster on error


def get_transfer_affected_players(draft_type: str, gw: int) -> Dict[str, List[Dict[str, Any]]]:
    """
    Get players affected by transfers for specific GW
    
    Args:
        draft_type: Type of draft ('UCL', 'EPL', 'TOP4')
        gw: Gameweek number
        
    Returns:
        Dictionary with 'transferred_in' and 'transferred_out' player lists
    """
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        transferred_in = []
        transferred_out = []
        
        # Check all rosters
        rosters = state.get("rosters", {})
        for manager, roster in rosters.items():
            for player in roster:
                transferred_in_gw = player.get("transferred_in_gw")
                transferred_out_gw = player.get("transferred_out_gw")
                
                if transferred_in_gw == gw:
                    transferred_in.append({
                        "manager": manager,
                        "player": player
                    })
                
                if transferred_out_gw == gw:
                    transferred_out.append({
                        "manager": manager,
                        "player": player
                    })
        
        # Check available transfer players
        available = transfer_system.get_available_transfer_players(state)
        for player in available:
            transferred_out_gw = player.get("transferred_out_gw")
            if transferred_out_gw == gw:
                transferred_out.append({
                    "manager": "Available Pool",
                    "player": player
                })
        
        return {
            "transferred_in": transferred_in,
            "transferred_out": transferred_out
        }
        
    except Exception:
        return {"transferred_in": [], "transferred_out": []}


def get_manager_roster_history(draft_type: str, manager: str) -> List[Dict[str, Any]]:
    """
    Get complete roster history for manager showing transfers
    
    Args:
        draft_type: Type of draft ('UCL', 'EPL', 'TOP4')
        manager: Manager name
        
    Returns:
        List of roster snapshots with GW information
    """
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        current_roster = state.get("rosters", {}).get(manager, [])
        transfer_history = transfer_system.get_transfer_history(state, manager)
        
        # Build roster evolution
        roster_history = []
        
        # Start with original roster (GW 1)
        original_players = [p for p in current_roster if p.get("transferred_in_gw", 1) == 1]
        if original_players:
            roster_history.append({
                "gw": 1,
                "action": "Initial Draft",
                "roster": original_players.copy(),
                "roster_size": len(original_players)
            })
        
        # Add transfer events
        for transfer in transfer_history:
            gw = transfer.get("gw")
            if transfer.get("action") == "pick_transfer_player":
                roster_history.append({
                    "gw": gw,
                    "action": "Picked Transfer Player",
                    "player_in": transfer.get("player"),
                    "roster_size": len([p for p in current_roster if gw in p.get("gws_active", [])])
                })
            else:
                roster_history.append({
                    "gw": gw,
                    "action": "Transfer",
                    "player_out": transfer.get("out_player"),
                    "player_in": transfer.get("in_player"),
                    "roster_size": len([p for p in current_roster if gw in p.get("gws_active", [])])
                })
        
        return sorted(roster_history, key=lambda x: x["gw"])
        
    except Exception:
        return []


def calculate_transfer_impact_score(draft_type: str, manager: str, start_gw: int, end_gw: int) -> Dict[str, Any]:
    """
    Calculate scoring impact of transfers for manager over GW range
    
    Args:
        draft_type: Type of draft ('UCL', 'EPL', 'TOP4')
        manager: Manager name
        start_gw: Starting gameweek
        end_gw: Ending gameweek
        
    Returns:
        Dictionary with transfer impact statistics
    """
    try:
        transfer_system = create_transfer_system(draft_type)
        state = transfer_system.load_state()
        
        transfers = transfer_system.get_transfer_history(state, manager)
        
        impact = {
            "total_transfers": len([t for t in transfers if t.get("action") != "pick_transfer_player"]),
            "players_picked": len([t for t in transfers if t.get("action") == "pick_transfer_player"]),
            "active_gws": list(range(start_gw, end_gw + 1)),
            "transfer_gws": [t.get("gw") for t in transfers if start_gw <= t.get("gw", 0) <= end_gw]
        }
        
        return impact
        
    except Exception:
        return {"total_transfers": 0, "players_picked": 0, "active_gws": [], "transfer_gws": []}
