#!/usr/bin/env python3
"""
Test script for Top-4 Transfer System
Simulates real manager interactions with the transfer system
"""

import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
import sys
import os

# Add the app to the path
sys.path.insert(0, str(Path(__file__).parent))

from draft_app import create_app
from draft_app.transfer_system import create_transfer_system, init_transfers_for_league
from draft_app.top4_services import load_state, save_state
from draft_app.top4_routes import get_transfer_order_from_results, create_backup


def create_test_environment():
    """Create a test environment with backup of original files"""
    print("🔧 Setting up test environment...")
    
    # Create test directory
    test_dir = Path("test_data")
    test_dir.mkdir(exist_ok=True)
    
    # Backup original state file
    original_state = Path("draft_state_top4.json")
    test_state = test_dir / "draft_state_top4_test.json"
    
    if original_state.exists():
        shutil.copy2(original_state, test_state)
        print(f"✅ Backed up original state to {test_state}")
    
    return test_dir, test_state


def create_test_roster_data():
    """Create test roster data with actual players"""
    print("📋 Creating test roster data...")
    
    # Load actual players from cache
    players_file = Path("data/cache/top4_players.json")
    if not players_file.exists():
        print("❌ top4_players.json not found")
        return None
    
    with open(players_file, 'r', encoding='utf-8') as f:
        players = json.load(f)
    
    print(f"📊 Loaded {len(players)} players from cache")
    
    # Group players by position
    players_by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for player in players:
        pos = player.get("position", "")
        if pos in players_by_pos:
            players_by_pos[pos].append(player)
    
    print("👥 Players by position:")
    for pos, pos_players in players_by_pos.items():
        print(f"  {pos}: {len(pos_players)} players")
    
    # Create test rosters for each manager
    managers = ["Ксана", "Саша", "Максим", "Андрей", "Сергей", "Тёма", "Женя", "Руслан"]
    test_rosters = {}
    
    # Position requirements: GK: 2, DEF: 5, MID: 5, FWD: 3 = 15 total
    position_limits = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
    
    used_players = set()
    
    for i, manager in enumerate(managers):
        roster = []
        print(f"\n🏗️ Creating roster for {manager}:")
        
        for pos, limit in position_limits.items():
            available = [p for p in players_by_pos[pos] if p["playerId"] not in used_players]
            selected = available[i*limit:(i+1)*limit]
            
            for j, player in enumerate(selected):
                if j < limit:
                    roster.append({
                        "playerId": player["playerId"],
                        "fullName": player["fullName"],
                        "clubName": player["clubName"],
                        "position": player["position"],
                        "league": player["league"],
                        "price": player.get("price", 0)
                    })
                    used_players.add(player["playerId"])
                    print(f"  {pos}: {player['fullName']} ({player['clubName']})")
        
        test_rosters[manager] = roster
        print(f"✅ {manager}: {len(roster)} players")
    
    return test_rosters


def create_test_state():
    """Create a complete test state with rosters"""
    print("\n🏗️ Creating test state...")
    
    test_rosters = create_test_roster_data()
    if not test_rosters:
        return None
    
    # Create complete draft state
    test_state = {
        "rosters": test_rosters,
        "draft_order": [
            "Ксана", "Саша", "Максим", "Андрей", "Сергей", "Тёма", "Женя", "Руслан",
            "Руслан", "Женя", "Тёма", "Сергей", "Андрей", "Максим", "Саша", "Ксана"
        ],
        "current_pick_index": 120,  # Draft completed
        "picks": [],
        "next_user": None,
        "draft_started_at": "2025-08-20T12:31:06",
        "draft_completed": True
    }
    
    # Add some picks for realism
    for i, manager in enumerate(["Ксана", "Саша", "Максим", "Андрей", "Сергей", "Тёма", "Женя", "Руслан"]):
        roster = test_rosters[manager]
        for j, player in enumerate(roster[:3]):  # First 3 picks per manager
            pick = {
                "user": manager,
                "player": player,
                "ts": f"2025-08-20T12:{31+i*8+j}:00"
            }
            test_state["picks"].append(pick)
    
    print(f"✅ Created test state with {len(test_state['rosters'])} managers")
    return test_state


def simulate_godmode_login():
    """Simulate godmode user actions"""
    print("\n👨‍💼 Simulating godmode login...")
    
    app = create_app()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_name'] = 'TestAdmin'
            sess['godmode'] = True
        
        print("✅ Godmode session created")
        return client


def test_transfer_order():
    """Test the transfer order calculation"""
    print("\n📊 Testing transfer order calculation...")
    
    try:
        transfer_order = get_transfer_order_from_results()
        print(f"✅ Transfer order: {' → '.join(transfer_order)}")
        return transfer_order
    except Exception as e:
        print(f"❌ Error calculating transfer order: {e}")
        return None


def test_transfer_window_opening():
    """Test opening the transfer window"""
    print("\n🚪 Testing transfer window opening...")
    
    try:
        # Get transfer order
        transfer_order = get_transfer_order_from_results()
        if not transfer_order:
            print("❌ Could not get transfer order")
            return False
        
        # Initialize transfer window
        success = init_transfers_for_league(
            draft_type="top4",
            participants=transfer_order,
            transfers_per_manager=3,  # 3 rounds of transfers
            position_limits={"GK": 2, "DEF": 5, "MID": 5, "FWD": 3},
            max_from_club=1
        )
        
        if success:
            print("✅ Transfer window opened successfully")
            
            # Check transfer system state
            ts = create_transfer_system("top4")
            state = ts.load_state()
            
            is_active = ts.is_transfer_window_active(state)
            current_manager = ts.get_current_transfer_manager(state)
            current_phase = ts.get_current_transfer_phase(state)
            
            print(f"📋 Window active: {is_active}")
            print(f"👤 Current manager: {current_manager}")
            print(f"🔄 Current phase: {current_phase}")
            
            return True
        else:
            print("❌ Failed to open transfer window")
            return False
            
    except Exception as e:
        print(f"❌ Error opening transfer window: {e}")
        import traceback
        traceback.print_exc()
        return False


def simulate_transfer_out(manager_name, player_id):
    """Simulate a transfer out action"""
    print(f"\n⬅️ Simulating Transfer Out: {manager_name} → Player {player_id}")
    
    try:
        ts = create_transfer_system("top4")
        state = ts.load_state()
        
        # Check if it's the manager's turn
        current_manager = ts.get_current_transfer_manager(state)
        current_phase = ts.get_current_transfer_phase(state)
        
        print(f"🔍 Current manager: {current_manager}, Phase: {current_phase}")
        
        if current_manager != manager_name:
            print(f"❌ It's not {manager_name}'s turn (current: {current_manager})")
            return False
        
        if current_phase != "out":
            print(f"❌ Wrong phase for transfer out (current: {current_phase})")
            return False
        
        # Execute transfer out
        updated_state = ts.transfer_player_out(state, manager_name, player_id, 1)
        ts.save_state(updated_state)
        
        print(f"✅ Transfer out successful for {manager_name}")
        
        # Check new state
        new_phase = ts.get_current_transfer_phase(updated_state)
        print(f"📋 New phase: {new_phase}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error in transfer out: {e}")
        import traceback
        traceback.print_exc()
        return False


def simulate_transfer_in(manager_name, player_id):
    """Simulate a transfer in action"""
    print(f"\n➡️ Simulating Transfer In: {manager_name} → Player {player_id}")
    
    try:
        ts = create_transfer_system("top4")
        state = ts.load_state()
        
        # Check if it's the manager's turn and correct phase
        current_manager = ts.get_current_transfer_manager(state)
        current_phase = ts.get_current_transfer_phase(state)
        
        print(f"🔍 Current manager: {current_manager}, Phase: {current_phase}")
        
        if current_manager != manager_name:
            print(f"❌ It's not {manager_name}'s turn (current: {current_manager})")
            return False
        
        if current_phase != "in":
            print(f"❌ Wrong phase for transfer in (current: {current_phase})")
            return False
        
        # Get available players
        available_players = ts.get_available_transfer_players(state)
        print(f"📊 Available players: {len(available_players)}")
        
        # Find the player
        target_player = None
        for player in available_players:
            if int(player.get("playerId", 0)) == player_id:
                target_player = player
                break
        
        if not target_player:
            print(f"❌ Player {player_id} not available for transfer in")
            return False
        
        # Execute transfer in
        updated_state = ts.transfer_player_in(state, manager_name, player_id, 1)
        ts.save_state(updated_state)
        
        print(f"✅ Transfer in successful for {manager_name}")
        
        # Check new state
        new_manager = ts.get_current_transfer_manager(updated_state)
        new_phase = ts.get_current_transfer_phase(updated_state)
        print(f"📋 Next manager: {new_manager}, Phase: {new_phase}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error in transfer in: {e}")
        import traceback
        traceback.print_exc()
        return False


def simulate_complete_transfer(manager_name):
    """Simulate a complete transfer (out + in) for a manager"""
    print(f"\n🔄 Simulating complete transfer for {manager_name}")
    
    try:
        ts = create_transfer_system("top4")
        state = ts.load_state()
        
        # Get manager's roster
        roster = state.get("rosters", {}).get(manager_name, [])
        if not roster:
            print(f"❌ No roster found for {manager_name}")
            return False
        
        # Pick first player for transfer out
        transfer_out_player = roster[0]
        player_out_id = int(transfer_out_player.get("playerId", 0))
        
        print(f"🎯 Transferring out: {transfer_out_player.get('fullName')} (ID: {player_out_id})")
        
        # Step 1: Transfer Out
        if not simulate_transfer_out(manager_name, player_out_id):
            return False
        
        # Step 2: Get available players for transfer in
        state = ts.load_state()
        available_players = ts.get_available_transfer_players(state)
        
        if not available_players:
            print("❌ No available players for transfer in")
            return False
        
        # Pick first available player with same position
        transfer_in_player = None
        out_position = transfer_out_player.get("position")
        
        for player in available_players:
            if player.get("position") == out_position:
                transfer_in_player = player
                break
        
        if not transfer_in_player:
            # If no same position, pick any available
            transfer_in_player = available_players[0]
        
        player_in_id = int(transfer_in_player.get("playerId", 0))
        print(f"🎯 Transferring in: {transfer_in_player.get('fullName')} (ID: {player_in_id})")
        
        # Step 3: Transfer In
        if not simulate_transfer_in(manager_name, player_in_id):
            return False
        
        print(f"✅ Complete transfer successful for {manager_name}")
        return True
        
    except Exception as e:
        print(f"❌ Error in complete transfer: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_transfer_history():
    """Test transfer history functionality"""
    print("\n📜 Testing transfer history...")
    
    try:
        ts = create_transfer_system("top4")
        state = ts.load_state()
        
        history = ts.get_transfer_history(state)
        print(f"📊 Transfer history entries: {len(history)}")
        
        for i, entry in enumerate(history[-5:]):  # Last 5 entries
            action = entry.get("action", "unknown")
            manager = entry.get("manager", "unknown")
            timestamp = entry.get("ts", "unknown")
            
            if action == "transfer_out":
                player = entry.get("out_player", {})
                player_name = player.get("fullName", "Unknown")
                print(f"  {i+1}. {timestamp}: {manager} transferred out {player_name}")
            elif action == "transfer_in":
                player = entry.get("in_player", {})
                player_name = player.get("fullName", "Unknown")
                print(f"  {i+1}. {timestamp}: {manager} transferred in {player_name}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing transfer history: {e}")
        return False


def test_transfer_schedule():
    """Test transfer schedule and status"""
    print("\n📅 Testing transfer schedule...")
    
    try:
        ts = create_transfer_system("top4")
        state = ts.load_state()
        
        is_active = ts.is_transfer_window_active(state)
        active_window = ts.get_active_transfer_window(state)
        current_manager = ts.get_current_transfer_manager(state)
        current_phase = ts.get_current_transfer_phase(state)
        
        print(f"📋 Window active: {is_active}")
        print(f"👤 Current manager: {current_manager}")
        print(f"🔄 Current phase: {current_phase}")
        
        if active_window:
            current_round = active_window.get("current_round", 1)
            total_rounds = active_window.get("total_rounds", 3)
            managers_order = active_window.get("managers_order", [])
            current_index = active_window.get("current_manager_index", 0)
            
            print(f"🏆 Round: {current_round}/{total_rounds}")
            print(f"👥 Managers order: {' → '.join(managers_order)}")
            print(f"📍 Current position: {current_index + 1}/{len(managers_order)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing transfer schedule: {e}")
        return False


def run_full_test():
    """Run the complete test suite"""
    print("🚀 Starting Top-4 Transfer System Test")
    print("=" * 50)
    
    # Setup test environment
    test_dir, test_state_file = create_test_environment()
    
    try:
        # Create test state
        test_state = create_test_state()
        if not test_state:
            print("❌ Failed to create test state")
            return False
        
        # Save test state
        with open("draft_state_top4.json", 'w', encoding='utf-8') as f:
            json.dump(test_state, f, ensure_ascii=False, indent=2)
        print("✅ Test state saved")
        
        # Test 1: Transfer order calculation
        transfer_order = test_transfer_order()
        if not transfer_order:
            print("❌ Transfer order test failed")
            return False
        
        # Test 2: Open transfer window
        if not test_transfer_window_opening():
            print("❌ Transfer window opening test failed")
            return False
        
        # Test 3: Simulate transfers for first few managers
        managers_to_test = transfer_order[:3]  # Test first 3 managers
        
        for manager in managers_to_test:
            print(f"\n🎮 Testing transfers for {manager}")
            if not simulate_complete_transfer(manager):
                print(f"❌ Transfer test failed for {manager}")
                break
            
            # Small delay for realism
            print("⏳ Waiting for next manager...")
        
        # Test 4: Transfer history
        if not test_transfer_history():
            print("❌ Transfer history test failed")
            return False
        
        # Test 5: Transfer schedule
        if not test_transfer_schedule():
            print("❌ Transfer schedule test failed")
            return False
        
        print("\n🎉 All tests completed successfully!")
        print("=" * 50)
        
        return True
        
    except Exception as e:
        print(f"❌ Test suite failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Cleanup: restore original state if it existed
        print("\n🧹 Cleaning up test environment...")
        if test_state_file.exists():
            shutil.copy2(test_state_file, "draft_state_top4.json")
            print("✅ Original state restored")


if __name__ == "__main__":
    success = run_full_test()
    sys.exit(0 if success else 1)
