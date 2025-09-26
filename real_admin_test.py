#!/usr/bin/env python3
"""
Real Admin Test with Authentication
Tests with real login, button clicks, and transfer window opening
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

def login_as_admin(session):
    """Login as GOD user with godmode"""
    print("🔐 Logging in as GOD (admin)...")
    
    try:
        # Get login page first
        response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/login", timeout=30)
        if response.status_code != 200:
            print(f"❌ Login page error: {response.status_code}")
            return False
        
        # Login with GOD credentials
        login_data = {
            "id": "10",
            "password": "1488"
        }
        
        response = session.post("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/login", 
                               data=login_data, timeout=30)
        
        if response.status_code == 302:  # Redirect after successful login
            print("✅ Login successful (redirect)")
            return True
        elif response.status_code == 200:
            # Check if we're still on login page (failed login) or redirected
            if "login" not in response.url.lower() and "Login" not in response.text:
                print("✅ Login successful")
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

def test_admin_interface(session):
    """Test admin interface after login"""
    print("🔍 Testing admin interface...")
    
    try:
        response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4", timeout=30)
        
        if response.status_code == 200:
            content = response.text
            
            # Check for admin elements
            has_transfer_button = "Открыть трансферное окно" in content
            has_undo_button = "Отменить последний пик" in content
            has_godmode_elements = "godmode" in content.lower()
            
            print(f"✅ Main page accessible: 200")
            print(f"✅ Transfer button visible: {has_transfer_button}")
            print(f"✅ Undo button visible: {has_undo_button}")
            print(f"✅ Godmode elements: {has_godmode_elements}")
            
            return has_transfer_button
        else:
            print(f"❌ Main page error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Interface test error: {e}")
        return False

def click_open_transfer_window_button(session):
    """Click the 'Открыть трансферное окно' button"""
    print("🎯 Clicking 'Открыть трансферное окно' button...")
    
    try:
        # Try the main route first
        response = session.post("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/open_transfer_window", 
                               timeout=30)
        
        print(f"📤 POST /top4/open_transfer_window: {response.status_code}")
        
        if response.status_code == 302:  # Redirect
            print("✅ Button click successful (redirect)")
            return True
        elif response.status_code == 200:
            print("✅ Button click successful")
            return True
        elif response.status_code == 404:
            print("⚠️ Route not found, trying alternative...")
            # Try alternative route
            response = session.post("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/transfers/top4/start-window", 
                                   timeout=30)
            print(f"📤 POST /transfers/top4/start-window: {response.status_code}")
            
            if response.status_code in [200, 302]:
                print("✅ Alternative button click successful")
                return True
            else:
                print(f"❌ Alternative button click failed: {response.status_code}")
                return False
        else:
            print(f"❌ Button click failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Button click error: {e}")
        return False

def verify_transfer_window_opened(session):
    """Verify that transfer window is now open"""
    print("🔍 Verifying transfer window opened...")
    
    try:
        # Check window status API
        response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/transfers/top4/window-status", 
                              timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            window_active = data.get("window_active", False)
            current_manager = data.get("current_manager")
            current_phase = data.get("current_phase")
            
            print(f"✅ Window status API: {response.status_code}")
            print(f"✅ Window active: {window_active}")
            print(f"✅ Current manager: {current_manager}")
            print(f"✅ Current phase: {current_phase}")
            
            if window_active:
                return True
        
        # Check main page for transfer interface
        response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4", timeout=30)
        
        if response.status_code == 200:
            content = response.text
            
            if "TRANSFER OUT" in content:
                print("✅ Transfer OUT interface detected on main page!")
                return True
            elif "TRANSFER IN" in content:
                print("✅ Transfer IN interface detected on main page!")
                return True
            elif "Трансферное окно активно" in content:
                print("✅ Transfer window active message detected!")
                return True
            else:
                print("⚠️ No transfer interface detected on main page")
        
        # Check schedule page
        response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/schedule", timeout=30)
        
        if response.status_code == 200:
            content = response.text
            if "Трансферное окно активно" in content:
                print("✅ Transfer window status on schedule page!")
                return True
        
        return False
        
    except Exception as e:
        print(f"❌ Verification error: {e}")
        return False

def test_manager_interface(session, manager_name, password):
    """Test interface as a regular manager"""
    print(f"👤 Testing interface as {manager_name}...")
    
    # Logout first
    try:
        session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/logout", timeout=10)
    except:
        pass
    
    # Login as manager
    try:
        # Map manager names to IDs from auth.json
        manager_ids = {
            "Тёма": "1", "Руслан": "2", "Сергей": "3", "Макс": "4",
            "Женя": "5", "Саша": "6", "Ксана": "7", "Андрей": "9"
        }
        
        manager_id = manager_ids.get(manager_name)
        if not manager_id:
            print(f"❌ Unknown manager: {manager_name}")
            return "unknown_manager"
        
        login_data = {
            "id": manager_id,
            "password": password
        }
        
        response = session.post("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/login", 
                               data=login_data, timeout=30)
        
        if response.status_code in [200, 302]:
            print(f"✅ Logged in as {manager_name}")
            
            # Check main page
            response = session.get("https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4", timeout=30)
            
            if response.status_code == 200:
                content = response.text
                
                if "TRANSFER OUT" in content:
                    print(f"✅ {manager_name} sees TRANSFER OUT interface")
                    return "transfer_out"
                elif "TRANSFER IN" in content:
                    print(f"✅ {manager_name} sees TRANSFER IN interface")
                    return "transfer_in"
                elif "Трансферное окно активно" in content:
                    print(f"✅ {manager_name} sees transfer window active message")
                    return "waiting"
                else:
                    print(f"📋 {manager_name} sees normal interface")
                    return "normal"
            else:
                print(f"❌ {manager_name} page error: {response.status_code}")
                return "error"
        else:
            print(f"❌ {manager_name} login failed: {response.status_code}")
            return "login_failed"
            
    except Exception as e:
        print(f"❌ {manager_name} test error: {e}")
        return "error"

def main():
    """Main test function"""
    print("🚀 REAL ADMIN TEST WITH AUTHENTICATION")
    print("=" * 70)
    
    # Create backup first
    backup_file = create_backup()
    if not backup_file:
        print("❌ Cannot proceed without backup")
        return False
    
    # Create session
    session = requests.Session()
    
    # Step 1: Login as admin
    login_success = login_as_admin(session)
    if not login_success:
        print("❌ Admin login failed")
        return False
    
    # Step 2: Test admin interface
    has_button = test_admin_interface(session)
    if not has_button:
        print("❌ Transfer button not visible")
        return False
    
    # Step 3: Click transfer window button
    button_clicked = click_open_transfer_window_button(session)
    if not button_clicked:
        print("❌ Button click failed")
        return False
    
    # Step 4: Wait for changes to propagate
    print("⏳ Waiting for transfer window to open...")
    time.sleep(3)
    
    # Step 5: Verify transfer window opened
    window_opened = verify_transfer_window_opened(session)
    
    # Step 6: Test manager interfaces
    managers_to_test = [
        ("Женя", "1987"),    # Should be first (lowest score)
        ("Ксана", "8523"),   # Should be last (highest score)
        ("Руслан", "7390")   # Should be waiting
    ]
    
    manager_results = {}
    for manager_name, password in managers_to_test:
        result = test_manager_interface(session, manager_name, password)
        manager_results[manager_name] = result
    
    # Final results
    print(f"\n" + "=" * 70)
    print("📋 FINAL RESULTS:")
    print(f"✅ Backup created: {backup_file}")
    print(f"✅ Admin login: {'SUCCESS' if login_success else 'FAILED'}")
    print(f"✅ Transfer button visible: {'YES' if has_button else 'NO'}")
    print(f"✅ Button click: {'SUCCESS' if button_clicked else 'FAILED'}")
    print(f"✅ Transfer window opened: {'YES' if window_opened else 'NO'}")
    
    print(f"\n👥 Manager Interface Results:")
    for manager, result in manager_results.items():
        print(f"  {manager}: {result}")
    
    if window_opened:
        print(f"\n🎉 TRANSFER WINDOW SUCCESSFULLY OPENED!")
        print(f"🎯 Expected first manager: Женя (lowest score)")
        print(f"🌐 Check: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4")
        print(f"📊 Monitor: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/schedule")
        print(f"📜 History: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/transfers/top4/history")
        
        return True
    else:
        print(f"\n❌ TRANSFER WINDOW OPENING FAILED")
        print("Check the logs above for details")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
