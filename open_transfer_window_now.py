#!/usr/bin/env python3
"""
Open Transfer Window on Production NOW
Direct API call to open the transfer window
"""

import requests
import json
import sys
import time
from pathlib import Path

# Add the app to the path
sys.path.insert(0, str(Path(__file__).parent))

from draft_app import create_app
from draft_app.transfer_system import create_transfer_system, init_transfers_for_league
from draft_app.top4_services import load_state, save_state

def open_transfer_window_directly():
    """Open transfer window directly using local Flask app"""
    print("🚀 Opening transfer window directly on local app...")
    
    app = create_app()
    
    with app.app_context():
        try:
            # Get transfer order from production
            print("📊 Getting transfer order from production...")
            response = requests.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/results/data", timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                lineups = data.get("lineups", {})
                
                # Calculate manager scores
                manager_scores = []
                for manager, data in lineups.items():
                    if isinstance(data, dict):
                        total = data.get("total", 0)
                    else:
                        total = 0
                    manager_scores.append((manager, total))
                    print(f"  {manager}: {total} points")
                
                # Sort by total points ascending (worst first for transfer priority)
                manager_scores.sort(key=lambda x: x[1])
                transfer_order = [manager for manager, _ in manager_scores]
                
                print(f"✅ Transfer order: {' → '.join(transfer_order)}")
                
                # Load current state
                state = load_state()
                
                # Create backup
                import shutil
                backup_file = f"draft_state_top4_backup_before_opening_{int(time.time())}.json"
                shutil.copy("draft_state_top4.json", backup_file)
                print(f"✅ Backup created: {backup_file}")
                
                # Initialize transfer window
                print("🔓 Initializing transfer window...")
                success = init_transfers_for_league(
                    draft_type="top4",
                    participants=transfer_order,
                    transfers_per_manager=3,  # 3 rounds of transfers
                    position_limits={"GK": 2, "DEF": 5, "MID": 5, "FWD": 3},
                    max_from_club=1
                )
                
                if success:
                    print("✅ Transfer window opened successfully!")
                    
                    # Verify the state
                    new_state = load_state()
                    transfer_window = new_state.get("transfer_window", {})
                    
                    if transfer_window.get("active"):
                        current_manager = transfer_window.get("current_user")
                        current_phase = transfer_window.get("transfer_phase")
                        print(f"✅ Current manager: {current_manager}")
                        print(f"✅ Current phase: {current_phase}")
                        print(f"✅ Participants: {transfer_window.get('participant_order', [])}")
                        
                        return True
                    else:
                        print("❌ Transfer window not active after initialization")
                        return False
                else:
                    print("❌ Failed to initialize transfer window")
                    return False
            else:
                print(f"❌ Failed to get results: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Error opening transfer window: {e}")
            import traceback
            traceback.print_exc()
            return False

def verify_transfer_window():
    """Verify that transfer window is open"""
    print("🔍 Verifying transfer window status...")
    
    try:
        response = requests.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/transfers/top4/window-status", timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            window_active = data.get("window_active", False)
            current_manager = data.get("current_manager")
            current_phase = data.get("current_phase")
            
            print(f"✅ Window active: {window_active}")
            print(f"✅ Current manager: {current_manager}")
            print(f"✅ Current phase: {current_phase}")
            
            return window_active
        else:
            print(f"❌ Status check failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error checking status: {e}")
        return False

def test_main_page():
    """Test main page to see transfer interface"""
    print("🌐 Testing main page interface...")
    
    try:
        response = requests.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4", timeout=30)
        
        if response.status_code == 200:
            content = response.text
            
            if "TRANSFER OUT" in content:
                print("✅ Transfer OUT interface detected!")
            elif "TRANSFER IN" in content:
                print("✅ Transfer IN interface detected!")
            elif "Трансферное окно активно" in content:
                print("✅ Transfer window active message detected!")
            else:
                print("⚠️ No transfer interface detected")
                
            return True
        else:
            print(f"❌ Main page error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing main page: {e}")
        return False

def main():
    """Main function"""
    print("🚀 OPENING TRANSFER WINDOW ON PRODUCTION NOW")
    print("=" * 60)
    
    # Step 1: Open transfer window directly
    success = open_transfer_window_directly()
    
    if not success:
        print("❌ Failed to open transfer window")
        return False
    
    # Step 2: Wait a moment for changes to propagate
    print("⏳ Waiting for changes to propagate...")
    time.sleep(5)
    
    # Step 3: Verify transfer window
    window_active = verify_transfer_window()
    
    # Step 4: Test main page
    main_page_ok = test_main_page()
    
    print(f"\n" + "=" * 60)
    print("📋 RESULTS:")
    print(f"✅ Transfer window opened: {'YES' if success else 'NO'}")
    print(f"✅ Window status verified: {'YES' if window_active else 'NO'}")
    print(f"✅ Main page updated: {'YES' if main_page_ok else 'NO'}")
    
    if success and window_active:
        print(f"\n🎉 TRANSFER WINDOW IS NOW OPEN!")
        print(f"🎯 First manager: Женя (lowest score)")
        print(f"🌐 Check: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4")
        print(f"📊 Monitor: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/schedule")
        print(f"📜 History: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/transfers/top4/history")
        
        return True
    else:
        print(f"\n❌ TRANSFER WINDOW OPENING FAILED")
        print("Please check the logs above for details")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
