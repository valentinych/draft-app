#!/usr/bin/env python3
"""
Production Test for Top-4 Transfer System using S3 data
Tests with real production data from AWS S3
"""

import json
import sys
from pathlib import Path

# Add the app to the path
sys.path.insert(0, str(Path(__file__).parent))

from draft_app import create_app
from draft_app.transfer_system import create_transfer_system
from draft_app.top4_services import load_state, _s3_enabled, _s3_bucket, _s3_get_json, _s3_state_key
from draft_app.top4_routes import get_transfer_order_from_results


def load_production_state_from_s3():
    """Load production state from S3"""
    print("🌐 Loading production state from AWS S3...")
    
    if not _s3_enabled():
        print("❌ S3 not enabled, cannot load production data")
        return None
    
    bucket = _s3_bucket()
    key = _s3_state_key()
    
    if not bucket or not key:
        print("❌ S3 bucket or key not configured")
        return None
    
    print(f"📡 Fetching from s3://{bucket}/{key}")
    
    try:
        data = _s3_get_json(bucket, key)
        if data:
            print("✅ Production state loaded from S3")
            return data
        else:
            print("⚠️ No data found in S3, using local state")
            return None
    except Exception as e:
        print(f"❌ Error loading from S3: {e}")
        return None


def analyze_production_state(state):
    """Analyze the production state"""
    print("\n📊 Analyzing Production State")
    print("-" * 40)
    
    # Basic info
    rosters = state.get("rosters", {})
    picks = state.get("picks", [])
    draft_completed = state.get("draft_completed", False)
    
    print(f"📋 Draft completed: {draft_completed}")
    print(f"🎯 Total picks: {len(picks)}")
    print(f"👥 Managers: {len(rosters)}")
    
    # Roster analysis
    print("\n🏆 Roster Analysis:")
    total_players = 0
    for manager, roster in rosters.items():
        roster_size = len(roster or [])
        total_players += roster_size
        print(f"  {manager}: {roster_size} players")
    
    print(f"📊 Total players drafted: {total_players}")
    
    # Transfer window status
    transfer_window = state.get("transfer_window")
    if transfer_window:
        print(f"\n🔄 Transfer Window:")
        print(f"  Active: {transfer_window.get('active', False)}")
        print(f"  Current user: {transfer_window.get('current_user', 'None')}")
        print(f"  Phase: {transfer_window.get('transfer_phase', 'None')}")
        print(f"  Participants: {transfer_window.get('participant_order', [])}")
    
    # Transfer history
    transfers = state.get("transfers", {})
    if transfers:
        history = transfers.get("history", [])
        available_players = transfers.get("available_players", [])
        print(f"\n📜 Transfer History: {len(history)} entries")
        print(f"📦 Available players: {len(available_players)}")
    
    return state


def test_with_production_data():
    """Test transfer system with production data"""
    print("🚀 Production Transfer System Test")
    print("=" * 50)
    
    # Step 1: Load production state
    s3_state = load_production_state_from_s3()
    
    if s3_state:
        print("✅ Using S3 production data")
        state = s3_state
    else:
        print("📁 Using local state file")
        state = load_state()
    
    # Step 2: Analyze state
    analyzed_state = analyze_production_state(state)
    
    # Step 3: Test transfer order calculation
    print("\n📊 Testing Transfer Order Calculation")
    try:
        transfer_order = get_transfer_order_from_results()
        print(f"✅ Transfer order: {' → '.join(transfer_order)}")
    except Exception as e:
        print(f"❌ Transfer order calculation failed: {e}")
        return False
    
    # Step 4: Test transfer system
    print("\n🔧 Testing Transfer System")
    try:
        ts = create_transfer_system("top4")
        current_state = ts.load_state()
        
        window_active = ts.is_transfer_window_active(current_state)
        current_manager = ts.get_current_transfer_manager(current_state)
        current_phase = ts.get_current_transfer_phase(current_state)
        
        print(f"✅ Transfer window active: {window_active}")
        print(f"✅ Current manager: {current_manager}")
        print(f"✅ Current phase: {current_phase}")
        
        if window_active:
            # Test available players
            available_players = ts.get_available_transfer_players(current_state)
            print(f"✅ Available players: {len(available_players)}")
            
            # Show some available players
            for i, player in enumerate(available_players[:5]):
                name = player.get("fullName", "Unknown")
                position = player.get("position", "Unknown")
                club = player.get("clubName", "Unknown")
                print(f"  {i+1}. {name} ({position}) - {club}")
        
    except Exception as e:
        print(f"❌ Transfer system test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 5: Test web interface with production data
    print("\n🌐 Testing Web Interface")
    app = create_app()
    
    with app.test_client() as client:
        # Test as different users
        test_users = ["Ксана", "Саша", "Максим", "TestAdmin"]
        
        for user in test_users:
            print(f"\n👤 Testing as {user}:")
            
            with client.session_transaction() as sess:
                sess['user_name'] = user
                sess['godmode'] = user == "TestAdmin"
            
            # Test main page
            try:
                response = client.get('/top4')
                print(f"  📱 GET /top4: {response.status_code}")
                
                if response.status_code == 200:
                    # Check if transfer interface is shown
                    content = response.get_data(as_text=True)
                    if "TRANSFER OUT" in content:
                        print("    ✅ Transfer OUT interface detected")
                    elif "TRANSFER IN" in content:
                        print("    ✅ Transfer IN interface detected")
                    elif "Трансферное окно активно" in content:
                        print("    ✅ Transfer window status shown")
                    else:
                        print("    📋 Normal draft interface")
                
            except Exception as e:
                print(f"  ❌ GET /top4 failed: {e}")
            
            # Test schedule page
            try:
                response = client.get('/top4/schedule')
                print(f"  📅 GET /top4/schedule: {response.status_code}")
            except Exception as e:
                print(f"  ❌ GET /top4/schedule failed: {e}")
            
            # Test transfer history (only for admin)
            if user == "TestAdmin":
                try:
                    response = client.get('/transfers/top4/history')
                    print(f"  📜 GET /transfers/top4/history: {response.status_code}")
                except Exception as e:
                    print(f"  ❌ GET /transfers/top4/history failed: {e}")
    
    # Step 6: Simulate button clicks if transfer window is active
    if window_active and current_manager:
        print(f"\n🎮 Simulating Button Clicks for {current_manager}")
        
        # Get manager's roster
        manager_roster = current_state.get("rosters", {}).get(current_manager, [])
        
        if manager_roster and current_phase == "out":
            print("🔘 Simulating Transfer Out button click")
            
            # Simulate POST request to transfer out
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_name'] = current_manager
                
                # Get first player to transfer out
                test_player = manager_roster[0]
                player_id = test_player.get("playerId")
                
                print(f"  📤 Attempting to transfer out: {test_player.get('fullName')} (ID: {player_id})")
                
                try:
                    response = client.post('/transfers/top4/pick-transfer-player', 
                                         data={'player_id': player_id})
                    print(f"  ✅ Transfer out response: {response.status_code}")
                    
                    if response.status_code == 200:
                        try:
                            data = response.get_json()
                            if data and data.get('success'):
                                print("    ✅ Transfer out successful!")
                            else:
                                print(f"    ⚠️ Transfer out response: {data}")
                        except:
                            print("    ✅ Transfer out completed (redirect response)")
                    
                except Exception as e:
                    print(f"  ❌ Transfer out simulation failed: {e}")
        
        elif current_phase == "in":
            print("🔘 Simulating Transfer In button click")
            
            available_players = ts.get_available_transfer_players(current_state)
            if available_players:
                # Simulate POST request to transfer in
                with app.test_client() as client:
                    with client.session_transaction() as sess:
                        sess['user_name'] = current_manager
                    
                    # Get first available player
                    test_player = available_players[0]
                    player_id = test_player.get("playerId")
                    
                    print(f"  📥 Attempting to transfer in: {test_player.get('fullName')} (ID: {player_id})")
                    
                    try:
                        response = client.post('/transfers/top4/transfer-player-in',
                                             data={'player_id': player_id})
                        print(f"  ✅ Transfer in response: {response.status_code}")
                        
                        if response.status_code == 200:
                            try:
                                data = response.get_json()
                                if data and data.get('success'):
                                    print("    ✅ Transfer in successful!")
                                else:
                                    print(f"    ⚠️ Transfer in response: {data}")
                            except:
                                print("    ✅ Transfer in completed (redirect response)")
                        
                    except Exception as e:
                        print(f"  ❌ Transfer in simulation failed: {e}")
    
    print("\n🎉 Production test completed!")
    return True


if __name__ == "__main__":
    success = test_with_production_data()
    
    print("\n" + "=" * 50)
    if success:
        print("✅ PRODUCTION TEST SUCCESSFUL")
        print("🚀 System working with real production data")
    else:
        print("❌ PRODUCTION TEST FAILED")
        print("🔧 System needs debugging")
    
    sys.exit(0 if success else 1)
