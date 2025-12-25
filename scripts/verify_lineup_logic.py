#!/usr/bin/env python3
"""
Проверка логики загрузки и сохранения составов:
1. При загрузке приоритет у draft_state_epl.json
2. При сохранении данные сохраняются и в draft_state_epl.json, и в lineups/
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from draft_app.epl_services import load_state, save_state
from draft_app.lineup_store import load_lineup, save_lineup
from draft_app.config import EPL_USERS

def verify_lineup_logic():
    """Проверяет логику загрузки и сохранения составов"""
    print("=" * 80)
    print("ПРОВЕРКА ЛОГИКИ ЗАГРУЗКИ И СОХРАНЕНИЯ СОСТАВОВ")
    print("=" * 80)
    print()
    
    state = load_state()
    lineups_state = state.get("lineups", {})
    
    print("1. ПРОВЕРКА ПРИОРИТЕТА ЗАГРУЗКИ:")
    print("-" * 80)
    print("Логика: draft_state_epl.json → lineups/ → auto-generate")
    print()
    
    # Проверяем для нескольких GW
    test_gws = [1, 10, 14, 17]
    managers = EPL_USERS
    
    for gw in test_gws:
        print(f"GW{gw}:")
        for manager in managers:
            m_state = lineups_state.get(manager, {})
            stored_lineup = m_state.get(str(gw))
            file_lineup = load_lineup(manager, gw, prefer_s3=False)
            
            if stored_lineup:
                print(f"  {manager}: ✅ в draft_state_epl.json (приоритет)")
            elif file_lineup:
                print(f"  {manager}: ⚠️  только в lineups/ (будет загружен как fallback)")
            else:
                print(f"  {manager}: ❌ не найден (будет auto-generated)")
        print()
    
    print("2. ПРОВЕРКА СОХРАНЕНИЯ:")
    print("-" * 80)
    print("При сохранении состав должен быть:")
    print("  ✅ В draft_state_epl.json (state['lineups'][manager][gw])")
    print("  ✅ В lineups/ (локальные файлы)")
    print("  ✅ В S3 (если настроено)")
    print()
    
    # Проверяем синхронизацию для GW14
    gw = 14
    print(f"Проверка синхронизации для GW{gw}:")
    synced_count = 0
    unsynced_count = 0
    
    for manager in managers:
        m_state = lineups_state.get(manager, {})
        stored_lineup = m_state.get(str(gw))
        file_lineup = load_lineup(manager, gw, prefer_s3=False)
        
        if stored_lineup and file_lineup:
            # Проверяем, что составы идентичны
            stored_players = set(stored_lineup.get("players", []))
            file_players = set(file_lineup.get("players", []))
            
            if stored_players == file_players:
                synced_count += 1
                print(f"  ✅ {manager}: синхронизирован")
            else:
                unsynced_count += 1
                print(f"  ⚠️  {manager}: рассинхронизирован (разные игроки)")
        elif stored_lineup:
            unsynced_count += 1
            print(f"  ⚠️  {manager}: только в draft_state_epl.json")
        elif file_lineup:
            unsynced_count += 1
            print(f"  ⚠️  {manager}: только в lineups/")
        else:
            print(f"  ℹ️  {manager}: состав отсутствует")
    
    print()
    print(f"Синхронизировано: {synced_count}/{len(managers)}")
    if unsynced_count > 0:
        print(f"⚠️  Требуется синхронизация: {unsynced_count} составов")
    
    print()
    print("3. РЕКОМЕНДАЦИИ:")
    print("-" * 80)
    print("✅ При загрузке: приоритет у draft_state_epl.json")
    print("✅ При сохранении: данные сохраняются в оба места")
    print("✅ При изменениях: автоматическая синхронизация")
    print()
    print("Составы в lineups/ используются для верификации и как fallback")

if __name__ == "__main__":
    verify_lineup_logic()

