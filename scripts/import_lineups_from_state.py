#!/usr/bin/env python3
"""
Скрипт для импорта составов из draft_state_epl (10) (1).json в файловую систему
"""
import json
import sys
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent.parent))

from draft_app.lineup_store import save_lineup
from draft_app.epl_services import get_roster_for_gw, load_state

def import_lineups_from_file(state_file_path: str):
    """Импортирует составы из файла state в файловую систему"""
    
    with open(state_file_path, 'r', encoding='utf-8') as f:
        state_data = json.load(f)
    
    lineups_data = state_data.get('lineups', {})
    
    # Загружаем текущий state для проверки трансферов
    current_state = load_state()
    
    imported_count = 0
    filtered_count = 0
    
    for manager, manager_lineups in lineups_data.items():
        for gw_str, lineup in manager_lineups.items():
            try:
                gw = int(gw_str)
            except (ValueError, TypeError):
                continue
            
            if not isinstance(lineup, dict):
                continue
            
            # Получаем ростер для этого GW с учетом трансферов
            roster_for_gw = get_roster_for_gw(current_state, manager, gw)
            valid_player_ids = {int(p.get("playerId") or p.get("id")) for p in roster_for_gw}
            
            # Фильтруем некорректные ID (больше 1000 или меньше 1)
            max_valid_id = 1000
            players = lineup.get("players", [])
            bench = lineup.get("bench", [])
            
            # Фильтруем игроков, которых нет в ростере для этого GW
            valid_players = [
                pid for pid in players 
                if isinstance(pid, int) and pid in valid_player_ids and 1 <= pid <= max_valid_id
            ]
            valid_bench = [
                pid for pid in bench 
                if isinstance(pid, int) and pid in valid_player_ids and 1 <= pid <= max_valid_id
            ]
            
            # Дополняем состав до 11 игроков, если не хватает
            if len(valid_players) < 11:
                # Сначала пытаемся взять из скамейки
                while len(valid_players) < 11 and valid_bench:
                    valid_players.append(valid_bench.pop(0))
                
                # Если все еще не хватает, берем из ростра
                if len(valid_players) < 11:
                    selected = set(valid_players + valid_bench)
                    for pl in roster_for_gw:
                        pid = int(pl.get("playerId") or pl.get("id"))
                        if pid not in selected and 1 <= pid <= max_valid_id:
                            if len(valid_players) < 11:
                                valid_players.append(pid)
                            else:
                                valid_bench.append(pid)
                            selected.add(pid)
                            if len(valid_players) >= 11:
                                break
            
            # Если были отфильтрованы игроки или дополнен состав, обновляем
            original_players_count = len(players)
            original_bench_count = len(bench)
            if (len(valid_players) != original_players_count or len(valid_bench) != original_bench_count or
                set(valid_players) != set(players) or set(valid_bench) != set(bench)):
                filtered_count += 1
                filtered_players = original_players_count - len([p for p in players if isinstance(p, int) and p in valid_player_ids and 1 <= p <= max_valid_id])
                filtered_bench_count = original_bench_count - len([p for p in bench if isinstance(p, int) and p in valid_player_ids and 1 <= p <= max_valid_id])
                added_players = len(valid_players) - (original_players_count - filtered_players)
                if filtered_players > 0 or filtered_bench_count > 0 or added_players > 0:
                    msg_parts = []
                    if filtered_players > 0:
                        msg_parts.append(f"отфильтровано {filtered_players} из старта")
                    if filtered_bench_count > 0:
                        msg_parts.append(f"{filtered_bench_count} из скамейки")
                    if added_players > 0:
                        msg_parts.append(f"дополнено {added_players} игроков")
                    print(f"  ⚠️  {manager} GW{gw}: {', '.join(msg_parts)}")
            
            # Создаем обновленный состав
            updated_lineup = {
                "formation": lineup.get("formation", "4-4-2"),
                "players": valid_players,
                "bench": valid_bench,
                "ts": lineup.get("ts"),
            }
            
            # Сохраняем состав
            save_lineup(manager, gw, updated_lineup)
            imported_count += 1
            print(f"  ✓ {manager} GW{gw}: {len(valid_players)} игроков в старте, {len(valid_bench)} на скамейке")
    
    print(f"\n✅ Импортировано составов: {imported_count}")
    print(f"⚠️  Отфильтровано составов: {filtered_count}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Импорт составов из state файла")
    parser.add_argument("file", help="Путь к файлу draft_state_epl (10) (1).json")
    
    args = parser.parse_args()
    
    if not Path(args.file).exists():
        print(f"❌ Файл {args.file} не найден!")
        sys.exit(1)
    
    import_lineups_from_file(args.file)

