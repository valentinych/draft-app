#!/usr/bin/env python3
"""
Скрипт для удаления некорректных playerId из draft_state_epl.json
"""
import json
from pathlib import Path
from typing import Set

def clean_invalid_ids(state_file: Path, output_file: Path, max_valid_id: int = 1000, specific_invalid_ids: Set[int] = None):
    """Удаляет некорректные playerId из state файла
    
    Args:
        state_file: Путь к исходному файлу
        output_file: Путь к выходному файлу
        max_valid_id: Максимальный валидный ID (по умолчанию 1000)
        specific_invalid_ids: Конкретные ID для удаления (например, {250112880, 250076574})
    """
    
    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    # Находим все некорректные ID
    invalid_ids: Set[int] = set()
    
    # Добавляем конкретные ID, если указаны
    if specific_invalid_ids:
        invalid_ids.update(specific_invalid_ids)
    
    # Проверяем ростеры
    for manager, roster in state.get('rosters', {}).items():
        for player in roster:
            pid = player.get('playerId') or player.get('id')
            if pid and (pid > max_valid_id or pid < 1):
                invalid_ids.add(pid)
    
    # Проверяем составы
    for manager, lineups in state.get('lineups', {}).items():
        for gw, lineup in lineups.items():
            players = lineup.get('players', [])
            bench = lineup.get('bench', [])
            for pid in players + bench:
                if pid and (pid > max_valid_id or pid < 1):
                    invalid_ids.add(pid)
    
    # Проверяем picks
    for pick in state.get('picks', []):
        player = pick.get('player', {})
        pid = player.get('playerId') or player.get('id')
        if pid and (pid > max_valid_id or pid < 1):
            invalid_ids.add(pid)
    
    if not invalid_ids:
        print("Некорректные ID не найдены")
        return
    
    print(f"Найдено некорректных ID: {len(invalid_ids)}")
    print(f"ID: {sorted(invalid_ids)}")
    
    # Удаляем из ростеров
    removed_from_rosters = 0
    for manager, roster in state.get('rosters', {}).items():
        original_len = len(roster)
        state['rosters'][manager] = [
            p for p in roster 
            if (p.get('playerId') or p.get('id')) not in invalid_ids
        ]
        removed_from_rosters += original_len - len(state['rosters'][manager])
    
    # Удаляем из составов
    removed_from_lineups = 0
    for manager, lineups in state.get('lineups', {}).items():
        for gw, lineup in lineups.items():
            players = lineup.get('players', [])
            bench = lineup.get('bench', [])
            
            original_players_len = len(players)
            original_bench_len = len(bench)
            
            lineup['players'] = [pid for pid in players if pid not in invalid_ids]
            lineup['bench'] = [pid for pid in bench if pid not in invalid_ids]
            
            removed_from_lineups += (original_players_len - len(lineup['players'])) + (original_bench_len - len(lineup['bench']))
    
    # Удаляем из picks
    removed_from_picks = 0
    original_picks_len = len(state.get('picks', []))
    state['picks'] = [
        pick for pick in state.get('picks', [])
        if (pick.get('player', {}).get('playerId') or pick.get('player', {}).get('id')) not in invalid_ids
    ]
    removed_from_picks = original_picks_len - len(state['picks'])
    
    print(f"\nУдалено:")
    print(f"  Из ростеров: {removed_from_rosters} записей")
    print(f"  Из составов: {removed_from_lineups} ID")
    print(f"  Из picks: {removed_from_picks} записей")
    
    # Сохраняем обновленный файл
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Очищенный файл сохранен в {output_file}")

if __name__ == "__main__":
    base_dir = Path(__file__).parent.parent
    state_file = base_dir / "draft_state_epl.json"
    output_file = base_dir / "draft_state_epl.json"
    
    if not state_file.exists():
        print(f"❌ Файл {state_file} не найден!")
        exit(1)
    
    # Конкретные некорректные ID для удаления
    specific_ids = {250112880, 250076574}
    
    clean_invalid_ids(state_file, output_file, specific_invalid_ids=specific_ids)

