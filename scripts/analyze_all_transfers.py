#!/usr/bin/env python3
"""
Детальный анализ всех трансферов из референсного файла
"""
import json
from pathlib import Path

reference_file = Path('/Users/ruslan.aharodnik/Downloads/draft_state_epl (10) (1).json')
if not reference_file.exists():
    print(f'Референсный файл не найден: {reference_file}')
    exit(1)

reference = json.load(open(reference_file, 'r', encoding='utf-8'))

print('=' * 80)
print('ДЕТАЛЬНЫЙ АНАЛИЗ ВСЕХ ТРАНСФЕРОВ')
print('=' * 80)
print()

# Получаем оригинальные ростеры из picks
original_rosters = {}
for pick in reference['picks']:
    manager = pick.get('user')
    if not manager:
        continue
    if manager not in original_rosters:
        original_rosters[manager] = []
    player = pick.get('player')
    if player:
        original_rosters[manager].append(player)

# Получаем ростеры после GW10
after_gw10_rosters = reference.get('rosters', {})

# Получаем lineups для определения точного GW трансферов
lineups = reference.get('lineups', {})

# Анализируем каждого менеджера
gw3_transfers = {}
gw10_transfers = {}

for manager in sorted(original_rosters.keys()):
    original_ids = {int(p.get('playerId') or p.get('id')) for p in original_rosters[manager]}
    after_gw10_ids = {int(p.get('playerId') or p.get('id')) for p in after_gw10_rosters.get(manager, [])}
    
    # Игроки, которые ушли
    removed = original_ids - after_gw10_ids
    # Игроки, которые пришли
    added = after_gw10_ids - original_ids
    
    if not removed and not added:
        continue
    
    # Проверяем lineups, чтобы определить, когда появились новые игроки
    manager_lineups = lineups.get(manager, {})
    
    # Определяем, какие трансферы были после GW3, а какие после GW10
    # Если игрок появился в GW11 или позже, значит трансфер после GW10
    # Если игрок появился в GW4-10, значит трансфер после GW3
    
    gw3_out = []
    gw3_in = []
    gw10_out = []
    gw10_in = []
    
    for removed_id in removed:
        removed_player = next((p for p in original_rosters[manager] if int(p.get('playerId') or p.get('id')) == removed_id), {})
        
        # Проверяем, когда этот игрок в последний раз был в lineup
        last_gw = 0
        for gw_str in sorted([k for k in manager_lineups.keys() if k.isdigit()], key=int):
            lineup = manager_lineups.get(gw_str, {})
            if removed_id in lineup.get('players', []) or removed_id in lineup.get('bench', []):
                last_gw = int(gw_str)
        
        # Если игрок был в GW3 или раньше, но не в GW10, значит ушел после GW3
        if last_gw <= 3:
            gw3_out.append(removed_id)
        else:
            gw10_out.append(removed_id)
    
    for added_id in added:
        added_player = next((p for p in after_gw10_rosters[manager] if int(p.get('playerId') or p.get('id')) == added_id), {})
        
        # Проверяем, когда этот игрок впервые появился в lineup
        first_gw = None
        for gw_str in sorted([k for k in manager_lineups.keys() if k.isdigit()], key=int):
            lineup = manager_lineups.get(gw_str, {})
            if added_id in lineup.get('players', []) or added_id in lineup.get('bench', []):
                first_gw = int(gw_str)
                break
        
        # Если игрок появился в GW4-10, значит трансфер после GW3
        # Если игрок появился в GW11 или позже, значит трансфер после GW10
        if first_gw and 4 <= first_gw <= 10:
            gw3_in.append(added_id)
        elif first_gw and first_gw >= 11:
            gw10_in.append(added_id)
        else:
            # Если не нашли в lineups, предполагаем GW3
            gw3_in.append(added_id)
    
    # Сопоставляем ушедших и пришедших игроков
    if gw3_out or gw3_in:
        gw3_transfers[manager] = []
        for i in range(min(len(gw3_out), len(gw3_in))):
            out_player = next((p for p in original_rosters[manager] if int(p.get('playerId') or p.get('id')) == gw3_out[i]), {})
            in_player = next((p for p in after_gw10_rosters[manager] if int(p.get('playerId') or p.get('id')) == gw3_in[i]), {})
            gw3_transfers[manager].append({
                'out': out_player,
                'in': in_player
            })
    
    if gw10_out or gw10_in:
        gw10_transfers[manager] = []
        for i in range(min(len(gw10_out), len(gw10_in))):
            out_player = next((p for p in after_gw10_rosters[manager] if int(p.get('playerId') or p.get('id')) == gw10_out[i]), {})
            in_player = next((p for p in after_gw10_rosters[manager] if int(p.get('playerId') or p.get('id')) == gw10_in[i]), {})
            gw10_transfers[manager].append({
                'out': out_player,
                'in': in_player
            })

# Выводим результаты
print('ТРАНСФЕРЫ ПОСЛЕ GW3:')
print('-' * 80)
for manager in sorted(gw3_transfers.keys()):
    print(f'\n{manager}:')
    for t in gw3_transfers[manager]:
        out_id_val = t['out'].get('playerId') or t['out'].get('id')
        in_id_val = t['in'].get('playerId') or t['in'].get('id')
        out_name = t['out'].get('fullName', f'ID {out_id_val}')
        in_name = t['in'].get('fullName', f'ID {in_id_val}')
        out_pos = t['out'].get('position', '?')
        in_pos = t['in'].get('position', '?')
        out_id = t['out'].get('playerId') or t['out'].get('id')
        in_id = t['in'].get('playerId') or t['in'].get('id')
        print(f'  {out_name} ({out_pos}, ID: {out_id}) → {in_name} ({in_pos}, ID: {in_id})')

print()
print('=' * 80)
print('ТРАНСФЕРЫ ПОСЛЕ GW10:')
print('-' * 80)
for manager in sorted(gw10_transfers.keys()):
    print(f'\n{manager}:')
    for t in gw10_transfers[manager]:
        out_id_val = t['out'].get('playerId') or t['out'].get('id')
        in_id_val = t['in'].get('playerId') or t['in'].get('id')
        out_name = t['out'].get('fullName', f'ID {out_id_val}')
        in_name = t['in'].get('fullName', f'ID {in_id_val}')
        out_pos = t['out'].get('position', '?')
        in_pos = t['in'].get('position', '?')
        out_id = t['out'].get('playerId') or t['out'].get('id')
        in_id = t['in'].get('playerId') or t['in'].get('id')
        print(f'  {out_name} ({out_pos}, ID: {out_id}) → {in_name} ({in_pos}, ID: {in_id})')

print()
print('=' * 80)
print(f'ИТОГО: GW3 - {sum(len(t) for t in gw3_transfers.values())} трансферов, GW10 - {sum(len(t) for t in gw10_transfers.values())} трансферов')

