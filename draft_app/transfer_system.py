"""
Unified Transfer System for all Draft types (UCL, EPL, TOP4)

Provides functionality for:
- Managing player transfers between teams
- Tracking GW-based scoring periods
- Making transfer-out players available for re-draft
- Maintaining transfer history
- Transfer window management
"""

from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from .services import load_json, save_json

# Transfer window schedules for different draft types
# Format: {gw: rounds_count} - rounds_count is number of transfer rounds available
TRANSFER_SCHEDULES = {
    "EPL": {
        3: 2,   # After GW3, 2 rounds of transfers
        10: 1,  # After GW10, 1 round of transfers
        17: 1,  # After GW17, 1 round of transfers
        24: 2,  # After GW24, 2 rounds of transfers
        29: 1,  # After GW29, 1 round of transfers
        34: 1,  # After GW34, 1 round of transfers
    },
    "UCL": {
        1: 1,   # After MD1, 1 round of transfers
        2: 1,   # After MD2, 1 round of transfers  
        3: 1,   # After MD3, 1 round of transfers
        4: 1,   # After MD4, 1 round of transfers
        5: 1,   # After MD5, 1 round of transfers
        6: 1,   # After MD6, 1 round of transfers
        7: 1,   # After MD7, 1 round of transfers
        8: 1,   # After MD8, 1 round of transfers (knockout phase)
    },
    "TOP4": {
        # Will be defined later
        # Example: 3: 1, 6: 1, 9: 1
    }
}


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
    
    def get_transfer_schedule(self) -> Dict[int, int]:
        """Get transfer schedule for this draft type"""
        return TRANSFER_SCHEDULES.get(self.draft_type, {})
    
    def is_transfer_window_active(self, state: Dict[str, Any]) -> bool:
        """Check if a transfer window is currently active"""
        state = self.ensure_transfer_structure(state)
        active_window = state["transfers"]["active_window"]
        
        print(f"[TransferSystem] is_transfer_window_active - draft_type: {self.draft_type}")
        print(f"[TransferSystem] active_window: {active_window}")
        
        # Check standard active_window format
        if active_window:
            # Check if window is still active (has remaining rounds)
            current_round = active_window.get("current_round", 0)
            total_rounds = active_window.get("total_rounds", 0)
            print(f"[TransferSystem] standard format - current_round: {current_round}, total_rounds: {total_rounds}")
            if current_round <= total_rounds:
                print(f"[TransferSystem] standard format active - returning True")
                return True
        
        # Check legacy transfer_window format (for UCL)
        legacy_window = state.get("transfer_window")
        print(f"[TransferSystem] legacy_window: {legacy_window}")
        if legacy_window and legacy_window.get("active"):
            print(f"[TransferSystem] legacy format active - returning True")
            return True
        
        print(f"[TransferSystem] no active window found - returning False")    
        return False
    
    def get_active_transfer_window(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get information about active transfer window"""
        state = self.ensure_transfer_structure(state)
        
        if not self.is_transfer_window_active(state):
            return None
            
        return state["transfers"]["active_window"]
    
    def start_transfer_window(self, state: Dict[str, Any], gw: int, managers_order: List[str]) -> bool:
        """Start a transfer window for specific GW"""
        schedule = self.get_transfer_schedule()
        rounds = schedule.get(gw, 0)
        
        if rounds <= 0:
            return False
            
        state = self.ensure_transfer_structure(state)
        
        # Check if window is already active for this GW
        active_window = state["transfers"]["active_window"]
        if active_window and active_window.get("gw") == gw:
            return False
            
        # Start new transfer window
        state["transfers"]["active_window"] = {
            "gw": gw,
            "total_rounds": rounds,
            "current_round": 1,
            "current_manager_index": 0,
            "managers_order": managers_order,
            "started_at": datetime.utcnow().isoformat(timespec="seconds")
        }
        
        return True
    
    def close_transfer_window(self, state: Dict[str, Any]) -> bool:
        """Close active transfer window"""
        state = self.ensure_transfer_structure(state)
        
        if not self.is_transfer_window_active(state):
            return False
            
        state["transfers"]["active_window"] = None
        return True
    
    def advance_transfer_turn(self, state: Dict[str, Any]) -> bool:
        """Advance to next manager in transfer window"""
        if not self.is_transfer_window_active(state):
            return False
        
        # Check if using legacy window format
        legacy_window = state.get("transfer_window")
        if legacy_window and legacy_window.get("active"):
            # Handle legacy window format
            participant_order = legacy_window.get("participant_order", [])
            current_index = legacy_window.get("current_index", 0)
            transfers_per_manager = legacy_window.get("transfers_per_manager", 1)
            transfers_completed = legacy_window.get("transfers_completed", {})
            
            # Find next manager who hasn't completed their transfers
            next_index = current_index + 1
            
            if next_index >= len(participant_order):
                # Check if anyone still has transfers left
                has_transfers_left = any(
                    transfers_completed.get(manager, 0) < transfers_per_manager
                    for manager in participant_order
                )
                
                if has_transfers_left:
                    # Start next round - find first manager with transfers left
                    for i, manager in enumerate(participant_order):
                        if transfers_completed.get(manager, 0) < transfers_per_manager:
                            legacy_window["current_index"] = i
                            legacy_window["current_user"] = manager
                            print(f"[TransferSystem] advance_transfer_turn - next turn: {manager} (index {i})")
                            return True
                else:
                    # All transfers completed, close window
                    legacy_window["active"] = False
                    print(f"[TransferSystem] advance_transfer_turn - closing legacy window")
                    return False
            else:
                # Move to next manager
                next_manager = participant_order[next_index]
                legacy_window["current_index"] = next_index
                legacy_window["current_user"] = next_manager
                print(f"[TransferSystem] advance_transfer_turn - next turn: {next_manager} (index {next_index})")
                return True
        else:
            # Handle standard active_window format
            active_window = state["transfers"]["active_window"]
            managers_order = active_window.get("managers_order", [])
            current_index = active_window.get("current_manager_index", 0)
            current_round = active_window.get("current_round", 1)
            total_rounds = active_window.get("total_rounds", 1)
            
            # Move to next manager
            next_index = current_index + 1
            
            if next_index >= len(managers_order):
                # End of round, start next round or close window
                next_round = current_round + 1
                if next_round > total_rounds:
                    # Close window
                    return self.close_transfer_window(state)
                else:
                    # Start next round
                    active_window["current_round"] = next_round
                    active_window["current_manager_index"] = 0
            else:
                active_window["current_manager_index"] = next_index
                
            return True
    
    def get_current_transfer_manager(self, state: Dict[str, Any]) -> Optional[str]:
        """Get manager who should make next transfer"""
        print(f"[TransferSystem] get_current_transfer_manager - draft_type: {self.draft_type}")
        
        # First try standard format
        active_window = self.get_active_transfer_window(state)
        
        if active_window:
            managers_order = active_window.get("managers_order", [])
            current_index = active_window.get("current_manager_index", 0)
            print(f"[TransferSystem] standard format - managers_order: {managers_order}, current_index: {current_index}")
            
            # Filter out empty strings from managers_order
            valid_managers = [m for m in managers_order if m and m.strip()]
            print(f"[TransferSystem] valid_managers after filtering: {valid_managers}")
            
            if current_index < len(valid_managers):
                manager = valid_managers[current_index]
                print(f"[TransferSystem] standard format returning: {manager}")
                return manager
            elif len(valid_managers) == 0:
                print(f"[TransferSystem] no valid managers in standard format, trying legacy")
            else:
                print(f"[TransferSystem] current_index {current_index} >= valid_managers length {len(valid_managers)}")
        
        # Fallback to legacy format (for UCL)
        legacy_window = state.get("transfer_window")
        print(f"[TransferSystem] legacy_window: {legacy_window}")
        if legacy_window and legacy_window.get("active"):
            participant_order = legacy_window.get("participant_order", [])
            current_index = legacy_window.get("current_index", 0)
            participants = [p for p in participant_order if p and p.strip()]
            print(f"[TransferSystem] legacy format - participants: {participants}, current_index: {current_index}")
            
            if not participants:
                # Fallback to default users if needed
                if self.draft_type.upper() == "UCL":
                    try:
                        from .config import UCL_USERS
                        participants = UCL_USERS
                        print(f"[TransferSystem] using UCL_USERS fallback: {participants}")
                    except ImportError:
                        participants = []
            
            if current_index < len(participants):
                manager = participants[current_index]
                print(f"[TransferSystem] legacy format returning: {manager}")
                return manager
        
        print(f"[TransferSystem] no manager found, returning None")        
        return None
    
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
                        current_gw: int,
                        force: bool = False) -> Dict[str, Any]:
        """Execute a player transfer"""
        state = self.ensure_transfer_structure(state)
        
        # Check if transfer window is active and manager can transfer
        if not force:
            if not self.is_transfer_window_active(state):
                raise ValueError("Трансферное окно не активно")
            
            current_manager = self.get_current_transfer_manager(state)
            if current_manager != manager:
                raise ValueError(f"Сейчас ход менеджера {current_manager}, а не {manager}")
        
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
        
        # Advance to next manager in transfer window (if not forced)
        if not force:
            self.advance_transfer_turn(state)
        
        return state
    
    def get_available_transfer_players(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get list of players available for transfer (transfer_out players + undrafted players for UCL)"""
        state = self.ensure_transfer_structure(state)
        available_players = list(state["transfers"]["available_players"])
        
        # For UCL, also include undrafted players
        if self.draft_type.upper() == "UCL":
            try:
                # Get all drafted players
                drafted_player_ids = set()
                rosters = state.get("rosters", {})
                for roster in rosters.values():
                    for player in roster:
                        player_id = player.get("playerId")
                        if player_id:
                            drafted_player_ids.add(int(player_id))
                
                # Get all UCL players from the main player list
                from .ucl import _json_load, _players_from_ucl, UCL_PLAYERS
                raw_players = _json_load(UCL_PLAYERS) or []
                all_ucl_players = _players_from_ucl(raw_players)
                
                # Add undrafted players to available list
                for player in all_ucl_players:
                    player_id = player.get("playerId")
                    if player_id and int(player_id) not in drafted_player_ids:
                        # Add undrafted player to available list
                        undrafted_player = {
                            "playerId": player["playerId"],
                            "fullName": player.get("fullName", player.get("name", "")),
                            "clubName": player.get("clubName", player.get("club", "")),
                            "position": player.get("position", ""),
                            "price": player.get("price", 0),
                            "status": "undrafted"
                        }
                        available_players.append(undrafted_player)
                        
            except Exception as e:
                print(f"[TransferSystem] Error adding undrafted UCL players: {e}")
        
        return available_players
    
    def pick_transfer_player(self, 
                           state: Dict[str, Any], 
                           manager: str, 
                           player_id: int, 
                           current_gw: int,
                           require_window: bool = True) -> Dict[str, Any]:
        """Pick a transfer_out player for a new team"""
        state = self.ensure_transfer_structure(state)
        
        # Check if manager can pick transfer players (less strict than transfers)
        if require_window:
            window_active = self.is_transfer_window_active(state)
            print(f"[TransferSystem] pick_transfer_player - window_active: {window_active}")
            if not window_active:
                raise ValueError("Трансферное окно неактивно")
        
        print(f"[TransferSystem] pick_transfer_player - searching for player_id: {player_id}")
        available_players = state["transfers"]["available_players"]
        print(f"[TransferSystem] pick_transfer_player - available_players count: {len(available_players)}")
        picked_player = None
        remaining_players = []
        
        for player in available_players:
            if int(player.get("playerId") or player.get("id", 0)) == player_id:
                picked_player = player.copy()
                print(f"[TransferSystem] pick_transfer_player - found player: {player.get('fullName', player.get('name', 'Unknown'))}")
            else:
                remaining_players.append(player)
        
        if not picked_player:
            print(f"[TransferSystem] pick_transfer_player - player {player_id} not found in available_players")
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
        
        print(f"[TransferSystem] pick_transfer_player - completed successfully for player {player_id}")
        return state
    
    def transfer_player_out(self,
                           state: Dict[str, Any],
                           manager: str,
                           player_id: int,
                           current_gw: int) -> Dict[str, Any]:
        """Transfer a player out from manager's roster to available pool"""
        state = self.ensure_transfer_structure(state)
        
        print(f"[TransferSystem] transfer_player_out - manager: {manager}, player_id: {player_id}")
        
        # Check if transfer window is active
        if not self.is_transfer_window_active(state):
            raise ValueError("Трансферное окно неактивно")
        
        # Check if it's manager's turn
        current_manager = self.get_current_transfer_manager(state)
        if current_manager != manager:
            raise ValueError(f"Сейчас ход менеджера {current_manager}, а не {manager}")
        
        # Get manager's roster
        rosters = state.setdefault("rosters", {})
        roster = rosters.setdefault(manager, [])
        
        print(f"[TransferSystem] transfer_player_out - roster size: {len(roster)}")
        print(f"[TransferSystem] transfer_player_out - all rosters keys: {list(rosters.keys())}")
        print(f"[TransferSystem] transfer_player_out - total rosters count: {sum(len(r) for r in rosters.values())}")
        print(f"[TransferSystem] transfer_player_out - state keys: {list(state.keys())}")
        print(f"[TransferSystem] transfer_player_out - state has picks: {len(state.get('picks', []))}")
        
        # Find and remove outgoing player
        out_player = None
        new_roster = []
        for player in roster:
            current_player_id = int(player.get("playerId") or player.get("id", 0))
            if current_player_id == player_id:
                out_player = player.copy()
                print(f"[TransferSystem] transfer_player_out - found player: {player.get('fullName', player.get('name', 'Unknown'))}")
                # Mark as transferred out
                out_player["status"] = "transfer_out"
                out_player["transferred_out_gw"] = current_gw
                # Add to available players pool
                state["transfers"]["available_players"].append(out_player)
            else:
                new_roster.append(player)
        
        if not out_player:
            print(f"[TransferSystem] transfer_player_out - player {player_id} not found in roster")
            raise ValueError(f"Player {player_id} not found in {manager}'s roster")
        
        # Update roster
        rosters[manager] = new_roster
        
        # Record in history as a transfer out event
        transfer_record = {
            "gw": current_gw,
            "manager": manager,
            "action": "transfer_out",
            "out_player": out_player,
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "draft_type": self.draft_type
        }
        
        state["transfers"]["history"].append(transfer_record)
        
        # Update transfers completed counter for legacy window
        legacy_window = state.get("transfer_window")
        if legacy_window and legacy_window.get("active"):
            transfers_completed = legacy_window.setdefault("transfers_completed", {})
            transfers_completed[manager] = transfers_completed.get(manager, 0) + 1
            print(f"[TransferSystem] transfer_player_out - {manager} completed {transfers_completed[manager]} transfers")
        
        # Advance to next manager's turn after successful transfer out
        self.advance_transfer_turn(state)
        
        print(f"[TransferSystem] transfer_player_out - completed successfully")
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
                         in_player: Dict[str, Any],
                         check_window: bool = True) -> tuple[bool, str]:
        """Validate if transfer is allowed"""
        state = self.ensure_transfer_structure(state)
        
        # Check transfer window status
        if check_window:
            if not self.is_transfer_window_active(state):
                return False, "Трансферное окно не активно"
            
            current_manager = self.get_current_transfer_manager(state)
            if current_manager != manager:
                if current_manager:
                    return False, f"Сейчас ход менеджера {current_manager}"
                else:
                    return False, "Трансферное окно завершено"
        
        rosters = state.get("rosters", {})
        roster = rosters.get(manager, [])
        
        # Check if player exists in roster
        out_player_exists = any(
            int(p.get("playerId", 0)) == out_player_id for p in roster
        )
        
        if not out_player_exists:
            return False, f"Игрок с ID {out_player_id} не найден в составе"
        
        # Check position constraints (implement based on draft type)
        out_player = next(p for p in roster if int(p.get("playerId", 0)) == out_player_id)
        out_position = out_player.get("position")
        in_position = in_player.get("position")
        
        if out_position != in_position:
            return False, f"Несоответствие позиций: {out_position} -> {in_position}"
        
        # Additional validation can be added here
        return True, "Трансфер разрешен"


# Factory function to create transfer system for different draft types
def create_transfer_system(draft_type: str) -> TransferSystem:
    """Create transfer system instance for specific draft type"""
    BASE_DIR = Path(__file__).resolve().parent.parent
    
    if draft_type.upper() == "UCL":
        return TransferSystem(
            "UCL", 
            BASE_DIR / "draft_state_ucl.json",
            s3_key=os.getenv("UCL_STATE_S3_KEY", "prod/draft_state_ucl.json")
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


# Alias for backward compatibility
def get_transfer_system(draft_type: str) -> TransferSystem:
    """Alias for create_transfer_system - for backward compatibility"""
    return create_transfer_system(draft_type)


def init_transfers_for_league(
    draft_type: str,
    participants: List[str],
    transfers_per_manager: int = 1,
    position_limits: Dict[str, int] = None,
    max_from_club: int = 1
) -> bool:
    """Initialize transfer window for a league"""
    try:
        ts = create_transfer_system(draft_type)
        state = ts.load_state()
        
        # Set up transfer window
        transfer_state = {
            "active": True,
            "current_user": participants[0] if participants else None,
            "participant_order": participants,
            "current_index": 0,
            "transfers_per_manager": transfers_per_manager,
            "transfers_completed": {user: 0 for user in participants},
            "position_limits": position_limits or {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3},
            "max_from_club": max_from_club,
            "started_at": datetime.utcnow().isoformat(),
        }
        
        state["transfer_window"] = transfer_state
        ts.save_state(state)
        return True
        
    except Exception as e:
        print(f"Error initializing transfers for {draft_type}: {e}")
        return False
