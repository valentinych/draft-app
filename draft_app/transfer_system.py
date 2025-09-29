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
from datetime import datetime, date
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
        1: 3,   # После первых туров, 3 круга трансферов (не змейкой)
    }
}


class TransferSystem:
    """Unified transfer system for all draft types"""
    
    def __init__(self, draft_type: str, state_file: Path, s3_key: Optional[str] = None):
        self.draft_type = draft_type.upper()
        self.state_file = state_file
        self.s3_key = s3_key
        self._player_index_cache: Optional[Dict[str, Dict[str, Any]]] = None
    
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

    def _get_player_index(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Lazily load player index for draft types that support it."""
        if self.draft_type != "TOP4":
            return None

        if self._player_index_cache is not None:
            return self._player_index_cache

        try:
            from .top4_services import load_players, players_index

            players = load_players()
            self._player_index_cache = players_index(players)
        except Exception as exc:  # pragma: no cover - defensive logging
            print(f"[TransferSystem] Failed to load TOP4 players: {exc}")
            self._player_index_cache = {}

        return self._player_index_cache

    def _enrich_player_details(self, player: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Fill in missing player metadata from cached sources when possible."""
        if not player:
            return player

        enriched = dict(player)

        full_name = enriched.get("fullName") or enriched.get("name")
        club = enriched.get("clubName") or enriched.get("club")
        position = enriched.get("position")

        has_placeholder_name = not full_name or str(full_name).startswith("Player_")
        has_placeholder_club = not club or str(club).lower() == "unknown"
        has_placeholder_position = not position or str(position).lower() == "unknown"

        if not (has_placeholder_name or has_placeholder_club or has_placeholder_position):
            return enriched

        player_id = enriched.get("playerId") or enriched.get("id")
        if player_id is None:
            return enriched

        index = self._get_player_index()
        if not index:
            return enriched

        lookup = index.get(str(player_id))
        if not lookup:
            return enriched

        # Only overwrite placeholder fields so we keep transfer-specific metadata
        if has_placeholder_name:
            enriched["fullName"] = lookup.get("fullName") or lookup.get("name", full_name)
        if has_placeholder_club:
            enriched["clubName"] = lookup.get("clubName") or lookup.get("club", club)
        if has_placeholder_position:
            enriched["position"] = lookup.get("position") or enriched.get("position")

        # Preserve canonical playerId if source provides one
        if not enriched.get("playerId") and lookup.get("playerId"):
            enriched["playerId"] = lookup.get("playerId")

        return enriched

    def _should_include_record_today(self, record: Dict[str, Any]) -> bool:
        """Return True if the record should be shown when filtering to today's activity."""
        if self.draft_type != "TOP4":
            return True

        ts = record.get("ts")
        if not ts:
            return True

        ts_clean = ts
        if isinstance(ts_clean, str) and ts_clean.endswith("Z"):
            ts_clean = ts_clean[:-1]

        record_date: Optional[date] = None
        try:
            record_dt = datetime.fromisoformat(ts_clean)
            record_date = record_dt.date()
        except ValueError:
            try:
                record_dt = datetime.strptime(ts_clean[:19], "%Y-%m-%dT%H:%M:%S")
                record_date = record_dt.date()
            except Exception:
                record_date = None

        if record_date is None:
            return True

        return record_date >= date.today()

    def get_transfer_schedule(self) -> Dict[int, int]:
        """Get transfer schedule for this draft type"""
        return TRANSFER_SCHEDULES.get(self.draft_type, {})
    
    def is_transfer_window_active(self, state: Dict[str, Any]) -> bool:
        """Check if a transfer window is currently active"""
        state = self.ensure_transfer_structure(state)
        active_window = state["transfers"]["active_window"]
        
        
        # Check standard active_window format
        if active_window:
            # Check if window is still active (has remaining rounds)
            current_round = active_window.get("current_round", 0)
            total_rounds = active_window.get("total_rounds", 0)
            if current_round <= total_rounds:
                pass
                return True
        
        # Check legacy transfer_window format (for UCL)
        legacy_window = state.get("transfer_window")
        if legacy_window and legacy_window.get("active"):
            pass
            return True
        
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
            "transfer_phase": "out",  # "out" or "in"
            "started_at": datetime.utcnow().isoformat(timespec="seconds")
        }
        
        return True
    
    def close_transfer_window(self, state: Dict[str, Any]) -> bool:
        """Close active transfer window"""
        state = self.ensure_transfer_structure(state)
        
        if not self.is_transfer_window_active(state):
            return False

        state["transfers"]["active_window"] = None

        legacy_window = state.get("transfer_window")
        if isinstance(legacy_window, dict):
            legacy_window["active"] = False
            legacy_window["current_user"] = None
            legacy_window["transfer_phase"] = "out"

        return True
    
    def advance_transfer_turn(self, state: Dict[str, Any]) -> bool:
        """Advance to next manager in transfer window"""
        if not self.is_transfer_window_active(state):
            return False
        
        # Check if using legacy window format
        legacy_window = state.get("transfer_window")
        active_window = state.get("transfers", {}).get("active_window")
        
        if legacy_window and legacy_window.get("active"):
            # Handle legacy window format with phases
            participant_order = legacy_window.get("participant_order", [])
            current_index = legacy_window.get("current_index", 0)
            transfers_per_manager = legacy_window.get("transfers_per_manager", 1)
            transfers_completed = legacy_window.get("transfers_completed", {})
            current_phase = legacy_window.get("transfer_phase", "out")
            
            # If currently in "out" phase, switch to "in" phase for same manager
            if current_phase == "out":
                legacy_window["transfer_phase"] = "in"
                return True
            
            # If in "in" phase, move to next manager and reset to "out" phase
            legacy_window["transfer_phase"] = "out"
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
                            return True
                else:
                    # All transfers completed, close window
                    legacy_window["active"] = False
                    return False
            else:
                # Move to next manager
                next_manager = participant_order[next_index]
                legacy_window["current_index"] = next_index
                legacy_window["current_user"] = next_manager
                return True
        else:
            # Handle standard active_window format
            active_window = state["transfers"]["active_window"]
            managers_order = active_window.get("managers_order", [])
            current_index = active_window.get("current_manager_index", 0)
            current_round = active_window.get("current_round", 1)
            total_rounds = active_window.get("total_rounds", 1)
            current_phase = active_window.get("transfer_phase", "out")
            
            # If currently in "out" phase, switch to "in" phase for same manager
            if current_phase == "out":
                active_window["transfer_phase"] = "in"
                return True
            
            # If in "in" phase, move to next manager and reset to "out" phase
            active_window["transfer_phase"] = "out"
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
                next_manager = managers_order[next_index] if next_index < len(managers_order) else "Unknown"
                
            return True
    
    def get_current_transfer_manager(self, state: Dict[str, Any]) -> Optional[str]:
        """Get manager who should make next transfer"""
        
        # First try standard format
        active_window = self.get_active_transfer_window(state)
        
        if active_window:
            managers_order = active_window.get("managers_order", [])
            current_index = active_window.get("current_manager_index", 0)
            # Filter out empty strings from managers_order
            valid_managers = [m for m in managers_order if m and m.strip()]
            
            if current_index < len(valid_managers):
                manager = valid_managers[current_index]
                return manager
            elif len(valid_managers) == 0:
                pass
            else:
                pass
        
        # Fallback to legacy format (for UCL)
        legacy_window = state.get("transfer_window")
        if legacy_window and legacy_window.get("active"):
            participant_order = legacy_window.get("participant_order", [])
            current_index = legacy_window.get("current_index", 0)
            participants = [p for p in participant_order if p and p.strip()]
            
            if not participants:
                # Fallback to default users if needed
                if self.draft_type.upper() == "UCL":
                    try:
                        from .config import UCL_USERS
                        participants = UCL_USERS
                    except ImportError:
                        participants = []
            
            if current_index < len(participants):
                manager = participants[current_index]
                return manager
        
        return None
    
    def get_current_transfer_phase(self, state: Dict[str, Any]) -> Optional[str]:
        """Get current transfer phase ('out' or 'in')"""
        active_window = self.get_active_transfer_window(state)
        if active_window:
            managers_order = active_window.get("managers_order", [])
            valid_managers = [m for m in managers_order if m and m.strip()]
            
            # Only use active_window if it has valid managers
            if valid_managers:
                phase = active_window.get("transfer_phase", "out")
                return phase
            else:
                pass
        
        # Legacy format - check if it has phase info
        legacy_window = state.get("transfer_window")
        if legacy_window and legacy_window.get("active"):
            phase = legacy_window.get("transfer_phase", "out")
            current_user = legacy_window.get("current_user", "Unknown")
            current_index = legacy_window.get("current_index", 0)
            return phase
        
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
            current_player_id = player.get("playerId") or player.get("id")
            # Compare both as strings and as ints if possible
            match = False
            if str(current_player_id) == str(out_player_id):
                match = True
            else:
                try:
                    if int(current_player_id) == int(out_player_id):
                        match = True
                except (ValueError, TypeError):
                    pass
            
            if match:
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
                pass
        
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
            if not window_active:
                raise ValueError("Трансферное окно неактивно")
        
        available_players = state["transfers"]["available_players"]
        picked_player = None
        remaining_players = []
        
        for player in available_players:
            current_player_id = player.get("playerId") or player.get("id")
            # Compare both as strings and as ints if possible
            match = False
            if str(current_player_id) == str(player_id):
                match = True
            else:
                try:
                    if int(current_player_id) == int(player_id):
                        match = True
                except (ValueError, TypeError):
                    pass
            
            if match:
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
    
    def transfer_player_out(self,
                           state: Dict[str, Any],
                           manager: str,
                           player_id: int,
                           current_gw: int) -> Dict[str, Any]:
        """Transfer a player out from manager's roster to available pool"""
        state = self.ensure_transfer_structure(state)
        
        
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
        
        
        # Find and remove outgoing player
        out_player = None
        new_roster = []
        for player in roster:
            current_player_id = player.get("playerId") or player.get("id")
            # Compare both as strings and as ints if possible
            match = False
            if str(current_player_id) == str(player_id):
                match = True
            else:
                try:
                    if int(current_player_id) == int(player_id):
                        match = True
                except (ValueError, TypeError):
                    pass
            
            if match:
                out_player = player.copy()
                # Mark as transferred out
                out_player["status"] = "transfer_out"
                out_player["transferred_out_gw"] = current_gw
                # Add to available players pool
                state["transfers"]["available_players"].append(out_player)
            else:
                new_roster.append(player)
        
        if not out_player:
            # FALLBACK: If player not found in roster (empty roster case), create a dummy player
            print(f"WARNING: Player {player_id} not found in {manager}'s roster, creating fallback")
            out_player = {
                "playerId": str(player_id),
                "fullName": f"Player_{player_id}",
                "clubName": "Unknown",
                "position": "Unknown",
                "league": "Mixed",
                "status": "transfer_out",
                "transferred_out_gw": current_gw
            }
            # Add to available players pool
            state["transfers"]["available_players"].append(out_player)
        
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
        
        # Switch to transfer in phase for the same manager
        active_window = state["transfers"].get("active_window")
        legacy_window = state.get("transfer_window")
        
        if active_window:
            active_window["transfer_phase"] = "in"
            if legacy_window and legacy_window.get("active"):
                legacy_window["transfer_phase"] = "in"
        elif legacy_window and legacy_window.get("active"):
            legacy_window["transfer_phase"] = "in"
            # Don't increment transfers_completed yet - wait for transfer_in
        else:
            # Old behavior for compatibility - advance turn
            self.advance_transfer_turn(state)
        
        return state
    
    def transfer_player_in(self,
                          state: Dict[str, Any],
                          manager: str,
                          player_id: int,
                          current_gw: int) -> Dict[str, Any]:
        """Transfer a player in from available pool to manager's roster"""
        state = self.ensure_transfer_structure(state)
        
        
        # Check if transfer window is active
        if not self.is_transfer_window_active(state):
            raise ValueError("Трансферное окно неактивно")
        
        # Check if it's manager's turn
        current_manager = self.get_current_transfer_manager(state)
        if current_manager != manager:
            raise ValueError(f"Сейчас ход менеджера {current_manager}, а не {manager}")
        
        # Check if we're in the "in" phase
        current_phase = self.get_current_transfer_phase(state)
        if current_phase != "in":
            raise ValueError("Сейчас фаза transfer out, а не transfer in")
        
        # Find and pick the player from all available transfer players (including undrafted for UCL)
        all_available_players = self.get_available_transfer_players(state)
        transfer_out_players = state["transfers"]["available_players"]
        picked_player = None
        is_from_transfer_out_pool = False
        
        # First check if player is in transfer out pool
        for i, player in enumerate(transfer_out_players):
            current_player_id = player.get("playerId") or player.get("id")
            # Compare both as strings and as ints if possible
            match = False
            if str(current_player_id) == str(player_id):
                match = True
            else:
                try:
                    if int(current_player_id) == int(player_id):
                        match = True
                except (ValueError, TypeError):
                    pass
            
            if match:
                picked_player = player.copy()
                is_from_transfer_out_pool = True
                # Remove from transfer out pool
                transfer_out_players.pop(i)
                break
        
        # If not found in transfer out pool, check all available players (including undrafted)
        if not picked_player:
            for player in all_available_players:
                current_player_id = player.get("playerId") or player.get("id")
                # Compare both as strings and as ints if possible
                match = False
                if str(current_player_id) == str(player_id):
                    match = True
                else:
                    try:
                        if int(current_player_id) == int(player_id):
                            match = True
                    except (ValueError, TypeError):
                        pass
                
                if match:
                    picked_player = player.copy()
                    break
        
        if not picked_player:
            raise ValueError(f"Player {player_id} not available for transfer in")
        
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
        
        # Note: Player already removed from transfer_out_players if it was there
        
        # Record in history as a transfer in event
        transfer_record = {
            "gw": current_gw,
            "manager": manager,
            "action": "transfer_in",
            "in_player": picked_player,
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "draft_type": self.draft_type
        }
        
        state["transfers"]["history"].append(transfer_record)
        
        # Handle legacy window format - increment transfers_completed and advance turn
        legacy_window = state.get("transfer_window")
        if legacy_window and legacy_window.get("active"):
            transfers_completed = legacy_window.setdefault("transfers_completed", {})
            transfers_completed[manager] = transfers_completed.get(manager, 0) + 1
            # Advance to next manager (this will handle phase transition)
            self.advance_transfer_turn(state)
        else:
            # Standard format - advance turn (this will reset phase to "out" and move to next manager)
            self.advance_transfer_turn(state)
        
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

        processed: List[Dict[str, Any]] = []
        for record in history:
            if not self._should_include_record_today(record):
                continue

            record_copy = dict(record)

            for key in ("out_player", "in_player", "player"):
                if record_copy.get(key):
                    record_copy[key] = self._enrich_player_details(dict(record_copy[key]))

            processed.append(record_copy)

        if manager:
            processed = [record for record in processed if record.get("manager") == manager]

        return processed
    
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
    max_from_club: int = 1,
    gw: Optional[int] = None,
    total_rounds: Optional[int] = None,
) -> bool:
    """Initialize transfer window for a league"""
    try:
        ts = create_transfer_system(draft_type)
        state = ts.load_state()
        state = ts.ensure_transfer_structure(state)

        cleaned_participants = [
            str(participant).strip()
            for participant in participants
            if participant and str(participant).strip()
        ]

        if not cleaned_participants:
            return False

        def _to_int(value: Optional[Any]) -> Optional[int]:
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                try:
                    return int(float(value))
                except (TypeError, ValueError):
                    return None

        effective_gw = _to_int(gw)
        if effective_gw is None:
            for key in ("current_matchday", "current_gw", "next_round", "current_round"):
                effective_gw = _to_int(state.get(key))
                if effective_gw is not None:
                    break

        if effective_gw is None:
            finished = state.get("finished_matchdays")
            if isinstance(finished, list):
                normalized = [_to_int(item) for item in finished]
                normalized = [item for item in normalized if item is not None]
                if normalized:
                    effective_gw = max(normalized)

        if effective_gw is None:
            effective_gw = 1

        schedule_rounds = ts.get_transfer_schedule().get(effective_gw, 0)
        effective_rounds = _to_int(total_rounds) or schedule_rounds or transfers_per_manager or 1

        transfers_section = state.setdefault("transfers", {})
        transfers_section.setdefault("history", [])
        transfers_section.setdefault("available_players", [])
        transfers_section.setdefault("legacy_windows", [])

        normalized_limits = dict(position_limits) if position_limits else {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}

        # Set up legacy transfer window structure (used by UCL main page)
        transfer_state = {
            "active": True,
            "current_user": cleaned_participants[0],
            "participant_order": cleaned_participants,
            "current_index": 0,
            "transfer_phase": "out",  # Always start with "out" phase
            "transfers_per_manager": transfers_per_manager,
            "transfers_completed": {user: 0 for user in cleaned_participants},
            "position_limits": normalized_limits,
            "max_from_club": max_from_club,
            "started_at": datetime.utcnow().isoformat(),
            "gw": effective_gw,
            "total_rounds": effective_rounds,
        }

        state["transfer_window"] = transfer_state

        # Mirror data to the modern transfer window format used by the unified UI
        transfers_section["active_window"] = {
            "gw": effective_gw,
            "total_rounds": effective_rounds,
            "current_round": 1,
            "current_manager_index": 0,
            "managers_order": cleaned_participants,
            "transfer_phase": "out",
            "started_at": datetime.utcnow().isoformat(timespec="seconds"),
            "metadata": {
                "transfers_per_manager": transfers_per_manager,
                "position_limits": normalized_limits,
                "max_from_club": max_from_club,
                "source": "init_transfers_for_league",
            },
        }

        ts.save_state(state)
        return True

    except Exception as e:
        print(f"Error initializing transfers for {draft_type}: {e}")
        return False
