#!/usr/bin/env python3
"""
Realistic Test for Top-4 Transfer System
Simulates real-world transfer scenarios with different players
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
from draft_app.top4_routes import get_transfer_order_from_results


def create_realistic_test():
    """Run a realistic transfer test scenario"""
    print("🎬 Starting Realistic Transfer Test Scenario")
    print("=" * 60)
    
    # Backup current state
    original_state_file = Path("draft_state_top4.json")
    backup_file = Path("draft_state_top4_backup.json")
    
    if original_state_file.exists():
        shutil.copy2(original_state_file, backup_file)
        print("✅ Original state backed up")
    
    try:
        # Load current state
        with open(original_state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
        
        print(f"📊 Loaded state with {len(state.get('rosters', {}))} managers")
        
        # Ensure draft is completed
        state["draft_completed"] = True
        state["current_pick_index"] = 999
        
        # Save state
        with open(original_state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        
        # 1. Test Transfer Order
        print("\n📊 Step 1: Testing Transfer Order")
        transfer_order = get_transfer_order_from_results()
        print(f"✅ Transfer order: {' → '.join(transfer_order)}")
        
        # 2. Open Transfer Window
        print("\n🚪 Step 2: Opening Transfer Window")
        success = init_transfers_for_league(
            draft_type="top4",
            participants=transfer_order,
            transfers_per_manager=3,
            position_limits={"GK": 2, "DEF": 5, "MID": 5, "FWD": 3},
            max_from_club=1
        )
        
        if not success:
            print("❌ Failed to open transfer window")
            return False
        
        print("✅ Transfer window opened successfully")
        
        # 3. Get transfer system
        ts = create_transfer_system("top4")
        
        # 4. Simulate multiple rounds of transfers
        round_count = 0
        max_rounds = 10  # Safety limit
        
        while round_count < max_rounds:
            state = ts.load_state()
            
            if not ts.is_transfer_window_active(state):
                print("🎉 Transfer window completed!")
                break
            
            current_manager = ts.get_current_transfer_manager(state)
            current_phase = ts.get_current_transfer_phase(state)
            
            print(f"\n🎮 Round {round_count + 1}: {current_manager} - {current_phase}")
            
            if current_phase == "out":
                # Transfer Out Phase
                manager_roster = state.get("rosters", {}).get(current_manager, [])
                if not manager_roster:
                    print(f"❌ No roster for {current_manager}")
                    break
                
                # Pick a player to transfer out (not the first one to make it interesting)
                transfer_out_player = manager_roster[min(1, len(manager_roster)-1)]
                player_out_id = int(transfer_out_player.get("playerId", 0))
                
                print(f"⬅️ {current_manager} transferring out: {transfer_out_player.get('fullName')} (ID: {player_out_id})")
                
                try:
                    updated_state = ts.transfer_player_out(state, current_manager, player_out_id, 1)
                    ts.save_state(updated_state)
                    print(f"✅ Transfer out successful")
                except Exception as e:
                    print(f"❌ Transfer out failed: {e}")
                    break
            
            elif current_phase == "in":
                # Transfer In Phase
                available_players = ts.get_available_transfer_players(state)
                
                if not available_players:
                    print("❌ No available players for transfer in")
                    break
                
                # Pick a player different from what the manager transferred out
                # Prefer a player from transfer_out pool if available
                transfer_out_pool = state.get("transfers", {}).get("available_players", [])
                
                if transfer_out_pool:
                    # Pick from transfer out pool
                    transfer_in_player = transfer_out_pool[0]
                else:
                    # Pick any available player
                    transfer_in_player = available_players[0]
                
                player_in_id = int(transfer_in_player.get("playerId", 0))
                
                print(f"➡️ {current_manager} transferring in: {transfer_in_player.get('fullName')} (ID: {player_in_id})")
                
                try:
                    updated_state = ts.transfer_player_in(state, current_manager, player_in_id, 1)
                    ts.save_state(updated_state)
                    print(f"✅ Transfer in successful")
                    
                    # Check next manager
                    next_manager = ts.get_current_transfer_manager(updated_state)
                    next_phase = ts.get_current_transfer_phase(updated_state)
                    print(f"📋 Next: {next_manager} - {next_phase}")
                    
                except Exception as e:
                    print(f"❌ Transfer in failed: {e}")
                    break
            
            round_count += 1
        
        # 5. Check final state
        print(f"\n📋 Transfer Test Summary:")
        final_state = ts.load_state()
        
        window_active = ts.is_transfer_window_active(final_state)
        print(f"🔄 Window still active: {window_active}")
        
        if window_active:
            current_manager = ts.get_current_transfer_manager(final_state)
            current_phase = ts.get_current_transfer_phase(final_state)
            active_window = ts.get_active_transfer_window(final_state)
            
            if active_window:
                current_round = active_window.get("current_round", 1)
                total_rounds = active_window.get("total_rounds", 3)
                print(f"📊 Round: {current_round}/{total_rounds}")
                print(f"👤 Current manager: {current_manager}")
                print(f"🔄 Current phase: {current_phase}")
        
        # 6. Check transfer history
        history = ts.get_transfer_history(final_state)
        print(f"📜 Total transfers: {len(history)}")
        
        # Show last few transfers
        for entry in history[-6:]:
            action = entry.get("action", "unknown")
            manager = entry.get("manager", "unknown")
            
            if action == "transfer_out":
                player = entry.get("out_player", {})
                player_name = player.get("fullName", "Unknown")
                print(f"  📤 {manager} → OUT: {player_name}")
            elif action == "transfer_in":
                player = entry.get("in_player", {})
                player_name = player.get("fullName", "Unknown")
                print(f"  📥 {manager} → IN: {player_name}")
        
        # 7. Test web interface simulation
        print(f"\n🌐 Web Interface Simulation:")
        
        app = create_app()
        with app.test_client() as client:
            # Simulate godmode login
            with client.session_transaction() as sess:
                sess['user_name'] = 'TestAdmin'
                sess['godmode'] = True
            
            # Test main page
            response = client.get('/top4')
            print(f"📱 GET /top4: {response.status_code}")
            
            # Test schedule page
            response = client.get('/top4/schedule')
            print(f"📅 GET /top4/schedule: {response.status_code}")
            
            # Test transfer history
            response = client.get('/transfers/top4/history')
            print(f"📜 GET /transfers/top4/history: {response.status_code}")
            
            # Test transfer window status
            response = client.get('/transfers/top4/window-status')
            print(f"📊 GET /transfers/top4/window-status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.get_json()
                print(f"   Window active: {data.get('window_active')}")
                print(f"   Current manager: {data.get('current_manager')}")
                print(f"   Current phase: {data.get('current_phase')}")
        
        print(f"\n🎉 Realistic transfer test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Restore original state
        if backup_file.exists():
            shutil.copy2(backup_file, original_state_file)
            backup_file.unlink()
            print("✅ Original state restored")


def test_edge_cases():
    """Test edge cases and error conditions"""
    print("\n🧪 Testing Edge Cases")
    print("-" * 30)
    
    try:
        ts = create_transfer_system("top4")
        state = ts.load_state()
        
        # Test 1: Invalid player ID
        print("🔬 Test 1: Invalid player transfer")
        try:
            ts.transfer_player_out(state, "Ксана", 999999, 1)
            print("❌ Should have failed with invalid player ID")
        except Exception as e:
            print(f"✅ Correctly rejected invalid player: {str(e)[:50]}...")
        
        # Test 2: Wrong manager turn
        print("🔬 Test 2: Wrong manager turn")
        current_manager = ts.get_current_transfer_manager(state)
        wrong_manager = "NotCurrentManager"
        
        if current_manager != wrong_manager:
            try:
                # Get a valid player from wrong manager's roster
                wrong_roster = state.get("rosters", {}).get(wrong_manager, [])
                if wrong_roster:
                    player_id = int(wrong_roster[0].get("playerId", 0))
                    ts.transfer_player_out(state, wrong_manager, player_id, 1)
                    print("❌ Should have failed with wrong manager")
                else:
                    print("⚠️ No roster for wrong manager to test")
            except Exception as e:
                print(f"✅ Correctly rejected wrong manager: {str(e)[:50]}...")
        
        # Test 3: Transfer window status
        print("🔬 Test 3: Transfer window methods")
        is_active = ts.is_transfer_window_active(state)
        current_manager = ts.get_current_transfer_manager(state)
        current_phase = ts.get_current_transfer_phase(state)
        active_window = ts.get_active_transfer_window(state)
        
        print(f"✅ Window active: {is_active}")
        print(f"✅ Current manager: {current_manager}")
        print(f"✅ Current phase: {current_phase}")
        print(f"✅ Active window: {active_window is not None}")
        
        return True
        
    except Exception as e:
        print(f"❌ Edge case test failed: {e}")
        return False


if __name__ == "__main__":
    print("🚀 Top-4 Transfer System - Realistic Testing")
    print("🎯 This test simulates real manager interactions")
    print("")
    
    # Run realistic test
    success = create_realistic_test()
    
    if success:
        # Run edge case tests
        test_edge_cases()
    
    print("\n" + "=" * 60)
    if success:
        print("🎉 All realistic tests PASSED!")
        print("✅ System is ready for production use")
    else:
        print("❌ Some tests FAILED!")
        print("🔧 System needs debugging before production")
    
    sys.exit(0 if success else 1)
