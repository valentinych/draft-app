"""
Unified Transfer System for all Draft types (UCL, EPL, TOP4)

Provides functionality for:
- Managing player transfers between teams
- Tracking GW-based scoring periods
- Making transfer-out players available for re-draft
- Maintaining transfer history
"""

from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from .services import load_json, save_json


class TransferSystem:
    """Unified transfer system for all draft types"""
    
    def __init__(self, draft_type: str, state_file: Path, s3_key: Optional[str] = None):
        self.draft_type = draft_type.upper()
        self.state_file = state_file
        self.s3_key = s3_key
    
    def load_state(self) -> Dict[str, Any]:
        """Load draft state from file"""
        return load_json(self.state_file, default={}, s3_key=self.s3_key)
    
    def save_state(self, state: Dict[str, Any]) -> None:
        """Save draft state to file"""
        save_json(self.state_file, state, s3_key=self.s3_key)
    
    def ensure_transfer_structure(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure proper transfer structure exists in state"""
        if "transfers" not in state:
            state["transfers"] = {
                "history": [],
                "available_players": [],
                "active_window": None
            }
        
        transfers = state["transfers"]
        if "history" not in transfers:
            transfers["history"] = []
        if "available_players" not in transfers:
            transfers["available_players"] = []
        if "active_window" not in transfers:
            transfers["active_window"] = None
            
        return state
    
    def normalize_player_data(self, player: Dict[str, Any], current_gw: int) -> Dict[str, Any]:
        """Normalize player data to include transfer tracking fields"""
        normalized = player.copy()
        
        # Ensure required fields exist
        if "status" not in normalized:
            normalized["status"] = "active"
        
        if "gws_active" not in normalized:
            # For existing players, assume they were active from GW 1
            normalized["gws_active"] = list(range(1, current_gw + 1))
        
        if "transferred_in_gw" not in normalized:
            normalized["transferred_in_gw"] = 1  # Default to draft start
            
        if "transferred_out_gw" not in normalized:
            normalized["transferred_out_gw"] = None
            
        return normalized
    
    def execute_transfer(self, 
                        state: Dict[str, Any], 
                        manager: str, 
                        out_player_id: int, 
                        in_player: Dict[str, Any], 
                        current_gw: int) -> Dict[str, Any]:
        """Execute a player transfer"""
        state = self.ensure_transfer_structure(state)
        
        # Get manager's roster
        rosters = state.setdefault("rosters", {})
        roster = rosters.setdefault(manager, [])
        
        # Find and remove outgoing player
        out_player = None
        new_roster = []
        for player in roster:
            player_id = int(player.get("playerId") or player.get("id", 0))
            if player_id == out_player_id:
                out_player = player.copy()
                # Mark as transferred out
                out_player["status"] = "transfer_out"
                out_player["transferred_out_gw"] = current_gw
                # Add to available players pool
                state["transfers"]["available_players"].append(out_player)
            else:
                new_roster.append(player)
        
        if not out_player:
            raise ValueError(f"Player {out_player_id} not found in {manager}'s roster")
        
        # Prepare incoming player
        in_player = self.normalize_player_data(in_player, current_gw)
        in_player["status"] = "transfer_in"
        in_player["transferred_in_gw"] = current_gw
        in_player["gws_active"] = list(range(current_gw, 39))  # Active from current GW onwards
        
        # Add incoming player to roster
        new_roster.append(in_player)
        rosters[manager] = new_roster
        
        # Record transfer in history
        transfer_record = {
            "gw": current_gw,
            "manager": manager,
            "out_player": out_player,
            "in_player": in_player,
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "draft_type": self.draft_type
        }
        
        state["transfers"]["history"].append(transfer_record)
        
        return state
    
    def get_available_transfer_players(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get list of players available for transfer (transfer_out players)"""
        state = self.ensure_transfer_structure(state)
        return state["transfers"]["available_players"]
    
    def pick_transfer_player(self, 
                           state: Dict[str, Any], 
                           manager: str, 
                           player_id: int, 
                           current_gw: int) -> Dict[str, Any]:
        """Pick a transfer_out player for a new team"""
        state = self.ensure_transfer_structure(state)
        
        available_players = state["transfers"]["available_players"]
        picked_player = None
        remaining_players = []
        
        for player in available_players:
            if int(player.get("playerId") or player.get("id", 0)) == player_id:
                picked_player = player.copy()
            else:
                remaining_players.append(player)
        
        if not picked_player:
            raise ValueError(f"Transfer player {player_id} not available")
        
        # Update player status
        picked_player["status"] = "active"
        picked_player["transferred_in_gw"] = current_gw
        # Extend gws_active from current GW
        existing_gws = picked_player.get("gws_active", [])
        picked_player["gws_active"] = existing_gws + list(range(current_gw, 39))
        
        # Add to manager's roster
        rosters = state.setdefault("rosters", {})
        roster = rosters.setdefault(manager, [])
        roster.append(picked_player)
        
        # Remove from available players
        state["transfers"]["available_players"] = remaining_players
        
        # Record in history as a pick event
        pick_record = {
            "gw": current_gw,
            "manager": manager,
            "action": "pick_transfer_player",
            "player": picked_player,
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "draft_type": self.draft_type
        }
        
        state["transfers"]["history"].append(pick_record)
        
        return state
    
    def get_player_active_gws(self, player: Dict[str, Any]) -> List[int]:
        """Get list of GWs when player should be counted for scoring"""
        return player.get("gws_active", [])
    
    def normalize_all_players(self, state: Dict[str, Any], current_gw: int) -> Dict[str, Any]:
        """Normalize all players in state to have transfer tracking fields"""
        rosters = state.get("rosters", {})
        
        for manager, roster in rosters.items():
            normalized_roster = []
            for player in roster:
                normalized_player = self.normalize_player_data(player, current_gw)
                normalized_roster.append(normalized_player)
            rosters[manager] = normalized_roster
        
        return state
    
    def get_transfer_history(self, state: Dict[str, Any], manager: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get transfer history, optionally filtered by manager"""
        state = self.ensure_transfer_structure(state)
        history = state["transfers"]["history"]
        
        if manager:
            return [record for record in history if record.get("manager") == manager]
        
        return history
    
    def validate_transfer(self, 
                         state: Dict[str, Any], 
                         manager: str, 
                         out_player_id: int, 
                         in_player: Dict[str, Any]) -> tuple[bool, str]:
        """Validate if transfer is allowed"""
        rosters = state.get("rosters", {})
        roster = rosters.get(manager, [])
        
        # Check if player exists in roster
        out_player_exists = any(
            int(p.get("playerId", 0)) == out_player_id for p in roster
        )
        
        if not out_player_exists:
            return False, f"Player {out_player_id} not found in roster"
        
        # Check position constraints (implement based on draft type)
        out_player = next(p for p in roster if int(p.get("playerId", 0)) == out_player_id)
        out_position = out_player.get("position")
        in_position = in_player.get("position")
        
        if out_position != in_position:
            return False, f"Position mismatch: {out_position} -> {in_position}"
        
        # Additional validation can be added here
        return True, "Transfer is valid"


# Factory function to create transfer system for different draft types
def create_transfer_system(draft_type: str) -> TransferSystem:
    """Create transfer system instance for specific draft type"""
    BASE_DIR = Path(__file__).resolve().parent.parent
    
    if draft_type.upper() == "UCL":
        return TransferSystem(
            "UCL", 
            BASE_DIR / "draft_state_ucl.json",
            s3_key=os.getenv("UCL_STATE_S3_KEY", "draft_state_ucl.json")
        )
    elif draft_type.upper() == "EPL":
        return TransferSystem(
            "EPL",
            BASE_DIR / "draft_state_epl.json", 
            s3_key=os.getenv("EPL_STATE_S3_KEY", "draft_state_epl.json")
        )
    elif draft_type.upper() == "TOP4":
        return TransferSystem(
            "TOP4",
            BASE_DIR / "draft_state_top4.json",
            s3_key=os.getenv("TOP4_STATE_S3_KEY", "draft_state_top4.json")  
        )
    else:
        raise ValueError(f"Unsupported draft type: {draft_type}")
