#!/usr/bin/env python3
"""
Скрипт для восстановления трансферов из сравнения двух состояний драфта
"""
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

def get_player_id(player: dict) -> int:
    """Получить ID игрока"""
    return int(player.get("playerId") or player.get("id") or 0)

def get_roster_ids(roster: List[dict]) -> Set[int]:
    """Получить множество ID игроков из ростра"""
    return {get_player_id(p) for p in roster if get_player_id(p) > 0}

def find_player_by_id(roster: List[dict], player_id: int) -> dict:
    """Найти игрока по ID"""
    for p in roster:
        if get_player_id(p) == player_id:
            return p
    return {}

def restore_transfers(current_file: Path, reference_file: Path) -> List[dict]:
    """
    Восстанавливает трансферы, сравнивая текущее состояние с референсным.
    Предполагается, что reference_file содержит состояние после GW10 (с трансферами после GW3 и GW10).
    current_file содержит текущее состояние (возможно, с откатами).
    """
    with open(current_file, 'r', encoding='utf-8') as f:
        current_state = json.load(f)
    
    with open(reference_file, 'r', encoding='utf-8') as f:
        reference_state = json.load(f)
    
    current_rosters = current_state.get("rosters", {})
    reference_rosters = reference_state.get("rosters", {})
    
    # Получаем оригинальные ростеры из picks (из reference файла, так как там больше picks)
    original_rosters: Dict[str, List[dict]] = {}
    picks = reference_state.get("picks", [])
    for pick in picks:
        manager = pick.get("user")
        if not manager:
            continue
        if manager not in original_rosters:
            original_rosters[manager] = []
        player = pick.get("player")
        if player:
            original_rosters[manager].append(player)
    
    transfers = []
    managers = set(current_rosters.keys()) | set(reference_rosters.keys())
    
    for manager in managers:
        current_roster = current_rosters.get(manager, [])
        reference_roster = reference_rosters.get(manager, [])
        original_roster = original_rosters.get(manager, [])
        
        current_ids = get_roster_ids(current_roster)
        reference_ids = get_roster_ids(reference_roster)
        original_ids = get_roster_ids(original_roster)
        
        # Трансферы после GW3: игроки, которые есть в reference, но не в original
        # И игроки, которые есть в original, но не в reference
        after_gw3_in = reference_ids - original_ids
        after_gw3_out = original_ids - reference_ids
        
        # Трансферы после GW10: игроки, которые есть в current, но не в reference
        # И игроки, которые есть в reference, но не в current
        # Но если current содержит откаты, то нужно наоборот
        after_gw10_in = current_ids - reference_ids
        after_gw10_out = reference_ids - current_ids
        
        # Восстанавливаем трансферы после GW3
        # Сопоставляем удаленных и добавленных игроков
        gw3_out_list = list(after_gw3_out)
        gw3_in_list = list(after_gw3_in)
        
        # Пытаемся сопоставить 1:1
        matched = set()
        for out_id in gw3_out_list:
            if out_id in matched:
                continue
            # Ищем первого доступного входящего
            for in_id in gw3_in_list:
                if in_id in matched:
                    continue
                out_player = find_player_by_id(original_roster, out_id)
                in_player = find_player_by_id(reference_roster, in_id)
                if out_player and in_player:
                    transfers.append({
                        "gw": 3,
                        "manager": manager,
                        "out": out_id,
                        "out_player": out_player,
                        "in": in_player,
                        "ts": "2025-09-15T12:00:00"
                    })
                    matched.add(out_id)
                    matched.add(in_id)
                    break
        
        # Оставшиеся трансферы после GW3 (только добавления или только удаления)
        for out_id in after_gw3_out:
            if out_id not in matched:
                out_player = find_player_by_id(original_roster, out_id)
                if out_player:
                    transfers.append({
                        "gw": 3,
                        "manager": manager,
                        "out": out_id,
                        "out_player": out_player,
                        "in": None,
                        "ts": "2025-09-15T12:00:00"
                    })
        
        for in_id in after_gw3_in:
            if in_id not in matched:
                in_player = find_player_by_id(reference_roster, in_id)
                if in_player:
                    transfers.append({
                        "gw": 3,
                        "manager": manager,
                        "out": None,
                        "out_player": None,
                        "in": in_player,
                        "ts": "2025-09-15T12:00:00"
                    })
        
        # Восстанавливаем трансферы после GW10
        gw10_out_list = list(after_gw10_out)
        gw10_in_list = list(after_gw10_in)
        
        matched_gw10 = set()
        for out_id in gw10_out_list:
            if out_id in matched_gw10:
                continue
            for in_id in gw10_in_list:
                if in_id in matched_gw10:
                    continue
                out_player = find_player_by_id(reference_roster, out_id)
                in_player = find_player_by_id(current_roster, in_id)
                if out_player and in_player:
                    transfers.append({
                        "gw": 10,
                        "manager": manager,
                        "out": out_id,
                        "out_player": out_player,
                        "in": in_player,
                        "ts": "2025-11-01T12:00:00"
                    })
                    matched_gw10.add(out_id)
                    matched_gw10.add(in_id)
                    break
        
        # Оставшиеся трансферы после GW10
        for out_id in after_gw10_out:
            if out_id not in matched_gw10:
                out_player = find_player_by_id(reference_roster, out_id)
                if out_player:
                    transfers.append({
                        "gw": 10,
                        "manager": manager,
                        "out": out_id,
                        "out_player": out_player,
                        "in": None,
                        "ts": "2025-11-01T12:00:00"
                    })
        
        for in_id in after_gw10_in:
            if in_id not in matched_gw10:
                in_player = find_player_by_id(current_roster, in_id)
                if in_player:
                    transfers.append({
                        "gw": 10,
                        "manager": manager,
                        "out": None,
                        "out_player": None,
                        "in": in_player,
                        "ts": "2025-11-01T12:00:00"
                    })
    
    return transfers

if __name__ == "__main__":
    base_dir = Path(__file__).parent.parent
    current_file = base_dir / "draft_state_epl.json"
    reference_file = Path("/Users/ruslan.aharodnik/Downloads/draft_state_epl (10) (1).json")
    
    if not reference_file.exists():
        print(f"Файл {reference_file} не найден!")
        exit(1)
    
    transfers = restore_transfers(current_file, reference_file)
    
    print("=" * 80)
    print("ВОССТАНОВЛЕННЫЕ ТРАНСФЕРЫ")
    print("=" * 80)
    
    # Группируем по GW
    gw3_transfers = [t for t in transfers if t["gw"] == 3]
    gw10_transfers = [t for t in transfers if t["gw"] == 10]
    
    if gw3_transfers:
        print("\n📋 ТРАНСФЕРЫ ПОСЛЕ GW3:")
        print("-" * 80)
        for t in gw3_transfers:
            manager = t["manager"]
            out_name = t["out_player"]["fullName"] if t["out_player"] else "N/A"
            in_name = t["in"]["fullName"]
            print(f"  {manager}: {out_name} → {in_name}")
    
    if gw10_transfers:
        print("\n📋 ТРАНСФЕРЫ ПОСЛЕ GW10:")
        print("-" * 80)
        for t in gw10_transfers:
            manager = t["manager"]
            out_name = t["out_player"]["fullName"] if t["out_player"] else "N/A"
            in_name = t["in"]["fullName"]
            print(f"  {manager}: {out_name} → {in_name}")
    
    if not transfers:
        print("\n⚠️  Трансферы не найдены или файлы идентичны")
    
    print("\n" + "=" * 80)

