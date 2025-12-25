#!/usr/bin/env python3
"""
Детальный анализ трансферов по составам и рострам
"""
import json
from pathlib import Path
from typing import Dict, List, Set

def get_player_id(player: dict) -> int:
    return int(player.get("playerId") or player.get("id") or 0)

def analyze_manager_transfers(manager: str, reference_state: dict) -> Dict[str, List]:
    """Анализирует трансферы для конкретного менеджера"""
    
    # Оригинальный ростер из picks
    original_roster = {}
    for pick in reference_state.get("picks", []):
        if pick.get("user") == manager:
            player = pick.get("player")
            if player:
                pid = get_player_id(player)
                original_roster[pid] = player.get("fullName")
    
    # Ростер после GW10
    roster_after_gw10 = {}
    for p in reference_state.get("rosters", {}).get(manager, []):
        pid = get_player_id(p)
        roster_after_gw10[pid] = p.get("fullName")
    
    # Анализируем составы по GW
    lineups = reference_state.get("lineups", {}).get(manager, {})
    
    # Игроки в GW1
    gw1_lineup = lineups.get("1", {})
    gw1_players = set(gw1_lineup.get("players", []) + gw1_lineup.get("bench", []))
    
    # Игроки в GW10
    gw10_lineup = lineups.get("10", {})
    gw10_players = set(gw10_lineup.get("players", []) + gw10_lineup.get("bench", []))
    
    # Игроки в GW11
    gw11_lineup = lineups.get("11", {})
    gw11_players = set(gw11_lineup.get("players", []) + gw11_lineup.get("bench", [])) if gw11_lineup else set()
    
    # Трансферы после GW3:
    # - Игроки, которые есть в GW10, но не в оригинальном ростре (добавлены после GW3)
    # - Игроки, которые есть в оригинале, но не в GW10 (удалены после GW3)
    gw3_added = gw10_players - set(original_roster.keys())
    gw3_removed = set(original_roster.keys()) - gw10_players
    
    # Трансферы после GW10:
    # - Игроки, которые есть в GW11, но не в GW10 (добавлены после GW10)
    # - Игроки, которые есть в GW10, но не в GW11 (удалены после GW10)
    gw10_added = gw11_players - gw10_players if gw11_players else set()
    gw10_removed = gw10_players - gw11_players if gw11_players else set()
    
    # Создаем словарь имен для игроков из GW10 состава
    gw10_names = {}
    for pid in gw10_players:
        gw10_names[pid] = roster_after_gw10.get(pid) or original_roster.get(pid) or "Unknown"
    
    # Сопоставляем удаленных и добавленных для GW3
    gw3_transfers = []
    gw3_removed_list = list(gw3_removed)
    gw3_added_list = list(gw3_added)
    
    # Пытаемся сопоставить 1:1
    matched = set()
    for out_id in gw3_removed_list:
        if out_id in matched:
            continue
        for in_id in gw3_added_list:
            if in_id in matched:
                continue
            out_name = original_roster.get(out_id, "Unknown")
            in_name = roster_after_gw10.get(in_id, "Unknown")
            gw3_transfers.append({
                "out": out_id,
                "out_name": out_name,
                "in": in_id,
                "in_name": in_name
            })
            matched.add(out_id)
            matched.add(in_id)
            break
    
    # Оставшиеся
    for out_id in gw3_removed_list:
        if out_id not in matched:
            gw3_transfers.append({
                "out": out_id,
                "out_name": original_roster.get(out_id, "Unknown"),
                "in": None,
                "in_name": None
            })
    
    for in_id in gw3_added_list:
        if in_id not in matched:
            gw3_transfers.append({
                "out": None,
                "out_name": None,
                "in": in_id,
                "in_name": roster_after_gw10.get(in_id, "Unknown")
            })
    
    # Трансферы после GW10
    gw10_transfers = []
    gw10_removed_list = list(gw10_removed)
    gw10_added_list = list(gw10_added)
    
    matched_gw10 = set()
    for out_id in gw10_removed_list:
        if out_id in matched_gw10:
            continue
        for in_id in gw10_added_list:
            if in_id in matched_gw10:
                continue
            out_name = gw10_names.get(out_id, "Unknown")
            in_name = roster_after_gw10.get(in_id, "Unknown")
            gw10_transfers.append({
                "out": out_id,
                "out_name": out_name,
                "in": in_id,
                "in_name": in_name
            })
            matched_gw10.add(out_id)
            matched_gw10.add(in_id)
            break
    
    for out_id in gw10_removed_list:
        if out_id not in matched_gw10:
            gw10_transfers.append({
                "out": out_id,
                "out_name": gw10_names.get(out_id, "Unknown"),
                "in": None,
                "in_name": None
            })
    
    for in_id in gw10_added_list:
        if in_id not in matched_gw10:
            gw10_transfers.append({
                "out": None,
                "out_name": None,
                "in": in_id,
                "in_name": roster_after_gw10.get(in_id, "Unknown")
            })
    
    return {
        "gw3": gw3_transfers,
        "gw10": gw10_transfers
    }

if __name__ == "__main__":
    reference_file = Path("/Users/ruslan.aharodnik/Downloads/draft_state_epl (10) (1).json")
    reference_state = json.load(open(reference_file, 'r', encoding='utf-8'))
    
    managers = sorted(reference_state.get("rosters", {}).keys())
    
    print("=" * 80)
    print("ВОССТАНОВЛЕННЫЕ ТРАНСФЕРЫ")
    print("=" * 80)
    
    all_gw3 = []
    all_gw10 = []
    
    for manager in managers:
        transfers = analyze_manager_transfers(manager, reference_state)
        all_gw3.extend([(manager, t) for t in transfers["gw3"]])
        all_gw10.extend([(manager, t) for t in transfers["gw10"]])
    
    if all_gw3:
        print("\n📋 ТРАНСФЕРЫ ПОСЛЕ GW3:")
        print("-" * 80)
        for manager, t in all_gw3:
            if t["out"] and t["in"]:
                print(f"  {manager}: {t['out_name']} → {t['in_name']}")
            elif t["out"]:
                print(f"  {manager}: {t['out_name']} → (удален)")
            elif t["in"]:
                print(f"  {manager}: (добавлен) → {t['in_name']}")
    
    if all_gw10:
        print("\n📋 ТРАНСФЕРЫ ПОСЛЕ GW10:")
        print("-" * 80)
        for manager, t in all_gw10:
            if t["out"] and t["in"]:
                print(f"  {manager}: {t['out_name']} → {t['in_name']}")
            elif t["out"]:
                print(f"  {manager}: {t['out_name']} → (удален)")
            elif t["in"]:
                print(f"  {manager}: (добавлен) → {t['in_name']}")
    
    print("\n" + "=" * 80)

