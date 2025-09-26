#!/usr/bin/env python3
"""
Fix Draft State and Open Transfer Window
Sets correct draft_completed state and opens transfer window with real authentication
"""

import requests
import json
import sys
import time
import shutil
from pathlib import Path
from datetime import datetime

def create_backup():
    """Create backup of current state"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"draft_state_top4_backup_{timestamp}.json"
    
    try:
        shutil.copy("draft_state_top4.json", backup_file)
        print(f"✅ Backup created: {backup_file}")
        return backup_file
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        return None

def fix_draft_state():
    """Fix local draft state to match production"""
    print("🔧 Fixing local draft state...")
    
    try:
        with open("draft_state_top4.json", "r", encoding="utf-8") as f:
            state = json.load(f)
        
        # Set draft as completed
        state["draft_completed"] = True
        
        # Save updated state
        with open("draft_state_top4.json", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        
        print("✅ Draft state fixed: draft_completed = true")
        return True
        
    except Exception as e:
        print(f"❌ Error fixing draft state: {e}")
        return False

def login_as_admin(session):
    """Login as GOD user with godmode"""
    print("🔐 Logging in as GOD (admin)...")
    
    try:
        # Login with GOD credentials (id=10)
        login_data = {
            "id": "10",
            "password": "1488"
        }
        
        response = session.post("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/login", 
                               data=login_data, timeout=30)
        
        if response.status_code == 302:  # Redirect after successful login
            print("✅ Admin login successful (redirect)")
            return True
        elif response.status_code == 200:
            # Check if we're redirected away from login page
            if "login" not in response.url.lower():
                print("✅ Admin login successful")
                return True
            else:
                print("❌ Login failed - still on login page")
                return False
        else:
            print(f"❌ Login failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Login error: {e}")
        return False

def check_transfer_button(session):
    """Check if transfer button is visible after login"""
    print("🔍 Checking for transfer button...")
    
    try:
        response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4", timeout=30)
        
        if response.status_code == 200:
            content = response.text
            
            # Look for transfer button
            has_transfer_button = "Открыть трансферное окно" in content
            has_draft_completed = "Драфт завершён" in content
            has_godmode = "godmode" in content.lower()
            
            print(f"✅ Page accessible: 200")
            print(f"✅ Draft completed badge: {has_draft_completed}")
            print(f"✅ Godmode detected: {has_godmode}")
            print(f"✅ Transfer button visible: {has_transfer_button}")
            
            if has_transfer_button:
                return True
            else:
                print("⚠️ Transfer button not found in page")
                # Let's look for the exact form
                if 'action="/top4/open_transfer_window"' in content:
                    print("✅ Found transfer form in HTML")
                    return True
                else:
                    print("❌ Transfer form not found")
                    return False
        else:
            print(f"❌ Page error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Button check error: {e}")
        return False

def open_transfer_window(session):
    """Open transfer window by clicking the button"""
    print("🎯 Opening transfer window...")
    
    try:
        # Try the main route
        response = session.post("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/open_transfer_window", 
                               timeout=30)
        
        print(f"📤 POST /top4/open_transfer_window: {response.status_code}")
        
        if response.status_code == 302:  # Redirect - likely success
            print("✅ Transfer window opened (redirect)")
            return True
        elif response.status_code == 200:
            print("✅ Transfer window opened")
            return True
        elif response.status_code == 404:
            print("⚠️ Main route not found, trying alternative...")
            
            # Try alternative route
            response = session.post("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/transfers/top4/start-window", 
                                   timeout=30)
            print(f"📤 POST /transfers/top4/start-window: {response.status_code}")
            
            if response.status_code in [200, 302]:
                print("✅ Transfer window opened via alternative route")
                return True
            else:
                print(f"❌ Alternative route failed: {response.status_code}")
                return False
        else:
            print(f"❌ Failed to open transfer window: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Transfer window opening error: {e}")
        return False

def verify_transfer_window(session):
    """Verify that transfer window is now open"""
    print("🔍 Verifying transfer window opened...")
    
    try:
        # Wait a moment for changes to propagate
        time.sleep(2)
        
        # Check status API
        response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/transfers/top4/window-status", 
                              timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            window_active = data.get("window_active", False)
            current_manager = data.get("current_manager")
            current_phase = data.get("current_phase")
            
            print(f"✅ Status API response: {response.status_code}")
            print(f"✅ Window active: {window_active}")
            print(f"✅ Current manager: {current_manager}")
            print(f"✅ Current phase: {current_phase}")
            
            if window_active and current_manager:
                return True, current_manager, current_phase
        
        # Check main page for transfer interface
        response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4", timeout=30)
        
        if response.status_code == 200:
            content = response.text
            
            if "TRANSFER OUT" in content:
                print("✅ TRANSFER OUT interface detected!")
                return True, "detected", "out"
            elif "TRANSFER IN" in content:
                print("✅ TRANSFER IN interface detected!")
                return True, "detected", "in"
            elif "Трансферное окно активно" in content:
                print("✅ Transfer window active message detected!")
                return True, "detected", "active"
        
        print("❌ No transfer interface detected")
        return False, None, None
        
    except Exception as e:
        print(f"❌ Verification error: {e}")
        return False, None, None

def test_first_manager(session):
    """Test interface as the first manager (Женя)"""
    print("👤 Testing as first manager (Женя)...")
    
    try:
        # Logout admin first
        try:
            session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/logout", timeout=10)
        except:
            pass
        
        # Login as Женя (id=5)
        login_data = {
            "id": "5",
            "password": "1987"
        }
        
        response = session.post("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/login", 
                               data=login_data, timeout=30)
        
        if response.status_code in [200, 302]:
            print("✅ Logged in as Женя")
            
            # Check main page
            response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4", timeout=30)
            
            if response.status_code == 200:
                content = response.text
                
                if "TRANSFER OUT" in content:
                    print("🎯 Женя sees TRANSFER OUT interface - PERFECT!")
                    return "transfer_out"
                elif "TRANSFER IN" in content:
                    print("🎯 Женя sees TRANSFER IN interface")
                    return "transfer_in"
                elif "Трансферное окно активно" in content:
                    print("🎯 Женя sees transfer window active")
                    return "waiting"
                else:
                    print("📋 Женя sees normal interface")
                    return "normal"
        
        return "error"
        
    except Exception as e:
        print(f"❌ Женя test error: {e}")
        return "error"

def main():
    """Main function"""
    print("🚀 FIX DRAFT STATE AND OPEN TRANSFER WINDOW")
    print("=" * 70)
    
    # Step 1: Create backup
    backup_file = create_backup()
    if not backup_file:
        print("❌ Cannot proceed without backup")
        return False
    
    # Step 2: Fix draft state
    state_fixed = fix_draft_state()
    if not state_fixed:
        print("❌ Cannot fix draft state")
        return False
    
    # Step 3: Create session and login as admin
    session = requests.Session()
    login_success = login_as_admin(session)
    if not login_success:
        print("❌ Admin login failed")
        return False
    
    # Step 4: Check for transfer button
    has_button = check_transfer_button(session)
    if not has_button:
        print("❌ Transfer button not visible")
        return False
    
    # Step 5: Open transfer window
    window_opened = open_transfer_window(session)
    if not window_opened:
        print("❌ Failed to open transfer window")
        return False
    
    # Step 6: Verify transfer window
    is_open, current_manager, current_phase = verify_transfer_window(session)
    
    # Step 7: Test first manager interface
    first_manager_status = "not_tested"
    if is_open:
        first_manager_status = test_first_manager(session)
    
    # Final results
    print(f"\n" + "=" * 70)
    print("🎉 FINAL RESULTS:")
    print(f"✅ Backup created: {backup_file}")
    print(f"✅ Draft state fixed: {'SUCCESS' if state_fixed else 'FAILED'}")
    print(f"✅ Admin login: {'SUCCESS' if login_success else 'FAILED'}")
    print(f"✅ Transfer button found: {'YES' if has_button else 'NO'}")
    print(f"✅ Transfer window opened: {'SUCCESS' if window_opened else 'FAILED'}")
    print(f"✅ Window verified: {'YES' if is_open else 'NO'}")
    print(f"✅ Current manager: {current_manager}")
    print(f"✅ Current phase: {current_phase}")
    print(f"✅ First manager (Женя) status: {first_manager_status}")
    
    if is_open and first_manager_status == "transfer_out":
        print(f"\n🎉 SUCCESS! TRANSFER WINDOW IS OPEN AND WORKING!")
        print(f"🎯 Женя can now make transfers!")
        print(f"🌐 Check: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4")
        print(f"📊 Monitor: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/schedule")
        print(f"📜 History: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/transfers/top4/history")
        return True
    else:
        print(f"\n⚠️ PARTIAL SUCCESS - Window opened but needs verification")
        return window_opened

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
