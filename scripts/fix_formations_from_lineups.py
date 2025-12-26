#!/usr/bin/env python3
"""
Исправление схем в составах на основе фактических данных из lineups/
"""
import json
import sys
from pathlib import Path
import urllib.request

sys.path.insert(0, str(Path(__file__).parent.parent))

from draft_app.lineup_store import load_lineup, save_lineup
from draft_app.epl_services import load_state, save_state
from draft_app.config import EPL_USERS

def get_fpl_players():
    """Получает информацию об игроках из FPL API"""
    url = 'https://fantasy.premierleague.com/api/bootstrap-static/'
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            bootstrap = json.loads(response.read().decode('utf-8'))
            players = bootstrap.get('elements', [])
            element_types = {t['id']: t['singular_name'] for t in bootstrap.get('element_types', [])}
            
            players_dict = {}
            for p in players:
                pid = p.get('id')
                if pid:
                    pos = element_types.get(p.get('element_type'), '?')
                    players_dict[pid] = {'position': pos}
            return players_dict
    except Exception as e:
        print(f'Ошибка загрузки FPL API: {e}')
        return {}

def detect_formation(players, fpl_players):
    """Определяет схему на основе фактического распределения"""
    gk = sum(1 for pid in players if 1 <= pid <= 1000 and fpl_players.get(pid, {}).get('position') == 'Goalkeeper')
    def_count = sum(1 for pid in players if 1 <= pid <= 1000 and fpl_players.get(pid, {}).get('position') == 'Defender')
    mid_count = sum(1 for pid in players if 1 <= pid <= 1000 and fpl_players.get(pid, {}).get('position') == 'Midfielder')
    fwd_count = sum(1 for pid in players if 1 <= pid <= 1000 and fpl_players.get(pid, {}).get('position') == 'Forward')
    
    if gk == 1:
        if def_count == 3 and mid_count == 4 and fwd_count == 3:
            return "3-4-3"
        elif def_count == 3 and mid_count == 5 and fwd_count == 2:
            return "3-5-2"
        elif def_count == 4 and mid_count == 3 and fwd_count == 3:
            return "4-3-3"
        elif def_count == 4 and mid_count == 4 and fwd_count == 2:
            return "4-4-2"
        elif def_count == 4 and mid_count == 5 and fwd_count == 1:
            return "4-5-1"
        elif def_count == 5 and mid_count == 3 and fwd_count == 2:
            return "5-3-2"
        elif def_count == 5 and mid_count == 4 and fwd_count == 1:
            return "5-4-1"
        else:
            return f"{def_count}-{mid_count}-{fwd_count}"
    return f"{def_count}-{mid_count}-{fwd_count}"

def main():
    print("=" * 80)
    print("ИСПРАВЛЕНИЕ СХЕМ НА ОСНОВЕ LINEUPS/")
    print("=" * 80)
    print()
    
    fpl_players = get_fpl_players()
    state = load_state()
    lineups_state = state.get('lineups', {})
    
    managers = EPL_USERS
    fixed_count = 0
    
    for manager in managers:
        print(f"{manager}:")
        for gw in range(11, 18):
            # Загружаем из lineups/
            file_lineup = load_lineup(manager, gw, prefer_s3=False)
            
            if not file_lineup:
                continue
            
            players = file_lineup.get('players', [])
            valid_players = [p for p in players if 1 <= p <= 1000]
            
            if len(valid_players) != 11:
                continue
            
            # Определяем фактическую схему
            detected_formation = detect_formation(valid_players, fpl_players)
            saved_formation = file_lineup.get('formation', '')
            
            # Проверяем в state
            m_state = lineups_state.setdefault(manager, {})
            state_lineup = m_state.get(str(gw))
            
            if detected_formation != saved_formation:
                print(f"  GW{gw}: {saved_formation} → {detected_formation}")
                file_lineup['formation'] = detected_formation
                save_lineup(manager, gw, file_lineup)
                
                # Обновляем в state
                if state_lineup:
                    state_lineup['formation'] = detected_formation
                else:
                    m_state[str(gw)] = file_lineup
                
                fixed_count += 1
            elif state_lineup and state_lineup.get('formation') != detected_formation:
                print(f"  GW{gw}: исправлено в state ({state_lineup.get('formation')} → {detected_formation})")
                state_lineup['formation'] = detected_formation
                fixed_count += 1
    
    # Сохраняем state
    if fixed_count > 0:
        save_state(state)
        print()
        print(f"✅ Исправлено схем: {fixed_count}")
    else:
        print()
        print("✅ Все схемы корректны!")

if __name__ == "__main__":
    main()

