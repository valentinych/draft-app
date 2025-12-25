#!/usr/bin/env python3
"""
Финальный список восстановленных трансферов
"""
import json
from pathlib import Path

reference = json.load(open('/Users/ruslan.aharodnik/Downloads/draft_state_epl (10) (1).json'))

print("=" * 80)
print("ВОССТАНОВЛЕННЫЕ ТРАНСФЕРЫ")
print("=" * 80)

# Функция для получения имени игрока
def get_player_name(pid: int, rosters: dict, original: dict) -> str:
    for p in rosters:
        if p.get('playerId') == pid:
            return p.get('fullName', 'Unknown')
    return original.get(pid, 'Unknown')

# Анализируем каждого менеджера
for manager in sorted(reference['rosters'].keys()):
    # Оригинальный ростер
    original = {}
    for pick in reference['picks']:
        if pick.get('user') == manager:
            p = pick.get('player')
            if p:
                original[p.get('playerId')] = p.get('fullName')
    
    # Ростер после GW10
    after_gw10_roster = reference['rosters'].get(manager, [])
    after_gw10 = {p.get('playerId'): p.get('fullName') for p in after_gw10_roster}
    
    # Составы
    lineups = reference.get('lineups', {}).get(manager, {})
    gw10_players = set(lineups.get('10', {}).get('players', []) + lineups.get('10', {}).get('bench', []))
    gw11_players = set(lineups.get('11', {}).get('players', []) + lineups.get('11', {}).get('bench', [])) if lineups.get('11') else set()
    
    # Трансферы после GW3
    gw3_out = sorted(set(original.keys()) - gw10_players)
    gw3_in = sorted(gw10_players - set(original.keys()))
    
    # Трансферы после GW10
    gw10_out = sorted(gw10_players - gw11_players) if gw11_players else []
    gw10_in = sorted(gw11_players - gw10_players) if gw11_players else []
    
    if gw3_out or gw3_in or gw10_out or gw10_in:
        print(f"\n{manager}:")
        
        if gw3_out or gw3_in:
            print("  После GW3:")
            # Сопоставляем 1:1
            for i in range(min(len(gw3_out), len(gw3_in))):
                out_name = original.get(gw3_out[i], 'Unknown')
                in_name = after_gw10.get(gw3_in[i], 'Unknown')
                print(f"    {out_name} → {in_name}")
            # Оставшиеся
            for i in range(len(gw3_in), len(gw3_out)):
                print(f"    {original.get(gw3_out[i], 'Unknown')} → (удален)")
            for i in range(len(gw3_out), len(gw3_in)):
                print(f"    (добавлен) → {after_gw10.get(gw3_in[i], 'Unknown')}")
        
        if gw10_out or gw10_in:
            print("  После GW10:")
            # Сопоставляем 1:1
            for i in range(min(len(gw10_out), len(gw10_in))):
                out_name = after_gw10.get(gw10_out[i]) or original.get(gw10_out[i], 'Unknown')
                in_name = after_gw10.get(gw10_in[i], 'Unknown')
                print(f"    {out_name} → {in_name}")
            # Оставшиеся
            for i in range(len(gw10_in), len(gw10_out)):
                out_name = after_gw10.get(gw10_out[i]) or original.get(gw10_out[i], 'Unknown')
                print(f"    {out_name} → (удален)")
            for i in range(len(gw10_out), len(gw10_in)):
                print(f"    (добавлен) → {after_gw10.get(gw10_in[i], 'Unknown')}")

print("\n" + "=" * 80)

