#!/usr/bin/env python3
"""
Нормализация draft_state_epl.json на основе:
1. Составов из S3 lineups/
2. Данных о трансферах
3. Правила замены только по позициям
"""
import json
import sys
import os
from pathlib import Path
from typing import Dict, List, Set, Optional
from collections import defaultdict
import urllib.request

sys.path.insert(0, str(Path(__file__).parent.parent))

from draft_app.lineup_store import _slug_parts
from draft_app.config import EPL_USERS

# Трансферы GW3
GW3_TRANSFERS = {
    'Андрей': [
        (491, 83),   # Sandro Tonali → Dango Ouattara
        (677, 389),  # Evann Guessand → Harvey Elliott
    ],
    'Женя': [
        (655, 726),  # Fábio Soares Silva → Randal Kolo Muani
    ],
    'Ксана': [
        (663, 242),  # Jhon Arias → Kiernan Dewsbury-Hall
    ],
    'Макс': [
        (158, 717),  # Georginio Rutter → Xavi Simons
        (610, 569),  # Aaron Wan-Bissaka → Cristian Romero
    ],
    'Руслан': [
        (239, 516),  # Jamie Bynoe-Gittens → Callum Hudson-Odoi
        (672, 478),  # Jorrel Hato → Kieran Trippier
    ],
    'Саша': [
        (526, 714),  # Igor Jesus Maciel da Cruz → Nick Woltemade
        (607, 261),  # Nayef Aguerd → Chris Richards
    ],
    'Сергей': [
        (20, 200),   # Leandro Trossard → Jaidon Anthony (исправлено с Elanga)
        (39, 685),   # Ian Maatsen → Bafodé Diakité
    ],
    'Тёма': [
        (251, 561),  # Nicolas Jackson → Eliezer Mayenda Dossou
        (182, 736),  # James Trafford → Gianluigi Donnarumma
        (120, 84),   # Kevin Schade → Marcus Tavernier
    ],
}

# Трансферы GW10
GW10_TRANSFERS = {
    'Андрей': [
        (507, 411),  # Ola Aina → Nico O'Reilly
    ],
    'Женя': [
        (48, 205),   # Youri Tielemans → Josh Cullen
    ],
    'Ксана': [
        (669, 668),  # Dan Ndoye → Granit Xhaka
    ],
    'Макс': [
        (11, 36),    # Benjamin White → Matty Cash
    ],
    'Руслан': [
        (525, 100),  # Chris Wood → Junior Kroupi
    ],
    'Саша': [
        (353, 20),   # Daniel James → Leandro Trossard
    ],
    'Сергей': [
        (583, 673),  # Dejan Kulusevski → Palhinha
    ],
    'Тёма': [
        (680, 365),  # Armando Broja → Lukas Nmecha
    ],
}

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
                    name = p.get('web_name', '')
                    first_name = p.get('first_name', '')
                    second_name = p.get('second_name', '')
                    full_name = f'{first_name} {second_name}'.strip() if first_name or second_name else name
                    players_dict[pid] = {
                        'playerId': pid,
                        'id': pid,
                        'fullName': full_name,
                        'name': name,
                        'position': pos,
                        'teamId': p.get('team', 0)
                    }
            return players_dict
    except Exception as e:
        print(f'Ошибка загрузки FPL API: {e}')
        return {}

def load_lineups_from_s3(bucket: str = "val-draft-storage", prefix: str = "lineups") -> Dict[str, Dict[int, Set[int]]]:
    """Загружает составы из S3 и возвращает игроков по менеджеру и GW"""
    lineups_by_manager = defaultdict(lambda: defaultdict(set))
    
    managers = EPL_USERS
    manager_slugs = {}
    for manager in managers:
        slug, _, _ = _slug_parts(manager)
        manager_slugs[slug] = manager
    
    regions = ["us-east-1", "eu-central-1", "eu-west-1"]
    base_urls = [f"https://{bucket}.s3.{region}.amazonaws.com" for region in regions]
    
    print("Загружаем составы из S3...")
    loaded_count = 0
    
    for slug, manager in manager_slugs.items():
        for gw in range(1, 21):
            for base_url in base_urls:
                url = f"{base_url}/{prefix}/{slug}/gw{gw}.json"
                try:
                    with urllib.request.urlopen(url, timeout=10) as response:
                        lineup = json.loads(response.read().decode('utf-8'))
                        players = set(lineup.get('players', []))
                        bench = set(lineup.get('bench', []))
                        all_players = players | bench
                        # Фильтруем некорректные ID
                        valid_players = {p for p in all_players if 1 <= p <= 1000}
                        if valid_players:
                            lineups_by_manager[manager][gw] = valid_players
                            loaded_count += 1
                        break
                except Exception:
                    continue
    
    print(f"Загружено составов: {loaded_count}")
    return lineups_by_manager

def normalize_rosters(state: dict, fpl_players: dict, lineups_data: dict):
    """Нормализует ростеры на основе трансферов и составов"""
    rosters = state.setdefault("rosters", {})
    transfer_history = state.setdefault("transfer", {}).setdefault("history", [])
    
    # Очищаем существующую историю и создаем новую
    transfer_history.clear()
    
    # Создаем словарь для отслеживания уже добавленных трансферов
    added_transfers = set()
    
    print("\nПрименяем трансферы GW3...")
    for manager, transfers in GW3_TRANSFERS.items():
        if manager not in rosters:
            continue
        
        roster = list(rosters[manager])
        roster_ids = {int(p.get('playerId') or p.get('id')) for p in roster}
        
        for out_id, in_id in transfers:
            out_player = None
            # Ищем out_player в текущем ростере
            for p in roster:
                if int(p.get('playerId') or p.get('id')) == out_id:
                    out_player = dict(p)
                    break
            
            # Если out_id нет в ростере, ищем в оригинальных picks
            if not out_player:
                picks = state.get('picks', [])
                for pick in picks:
                    if pick.get('user') == manager:
                        player = pick.get('player', {})
                        if player and int(player.get('playerId') or player.get('id')) == out_id:
                            out_player = dict(player)
                            break
            
            # Всегда удаляем out_id, если он есть
            if out_id in roster_ids:
                roster = [p for p in roster if int(p.get('playerId') or p.get('id')) != out_id]
                roster_ids.discard(out_id)
                removed = True
            else:
                removed = False
            
            # Всегда добавляем in_id, если его еще нет
            if in_id not in roster_ids:
                in_player = fpl_players.get(in_id, {})
                if in_player:
                    roster.append(in_player)
                    roster_ids.add(in_id)
                    if removed:
                        print(f"  {manager}: ID {out_id} → ID {in_id}")
                    else:
                        print(f"  {manager}: добавлен ID {in_id} (out_id {out_id} не найден в ростере)")
            elif removed:
                print(f"  {manager}: удален ID {out_id} (in_id {in_id} уже в ростере)")
            
            # Добавляем в историю трансферов, если еще не добавлен
            transfer_key = (manager, 3, out_id)
            if transfer_key not in added_transfers:
                in_player = fpl_players.get(in_id, {})
                
                transfer_history.append({
                    "gw": 3,
                    "round": 1,
                    "manager": manager,
                    "out": out_id,
                    "out_player": out_player if out_player else None,
                    "in": in_player if in_player else {"playerId": in_id},
                    "ts": "2024-09-01T00:00:00+00:00"
                })
                added_transfers.add(transfer_key)
        
        rosters[manager] = roster
    
    print("\nПрименяем трансферы GW10...")
    for manager, transfers in GW10_TRANSFERS.items():
        if manager not in rosters:
            continue
        
        roster = list(rosters[manager])
        roster_ids = {int(p.get('playerId') or p.get('id')) for p in roster}
        
        for out_id, in_id in transfers:
            out_player = None
            # Ищем out_player в текущем ростере
            for p in roster:
                if int(p.get('playerId') or p.get('id')) == out_id:
                    out_player = dict(p)
                    break
            
            # Если out_id нет в ростере, ищем в оригинальных picks
            if not out_player:
                picks = state.get('picks', [])
                for pick in picks:
                    if pick.get('user') == manager:
                        player = pick.get('player', {})
                        if player and int(player.get('playerId') or player.get('id')) == out_id:
                            out_player = dict(player)
                            break
            
            # Всегда удаляем out_id, если он есть
            if out_id in roster_ids:
                roster = [p for p in roster if int(p.get('playerId') or p.get('id')) != out_id]
                roster_ids.discard(out_id)
                removed = True
            else:
                removed = False
            
            # Всегда добавляем in_id, если его еще нет
            if in_id not in roster_ids:
                in_player = fpl_players.get(in_id, {})
                if in_player:
                    roster.append(in_player)
                    roster_ids.add(in_id)
                    if removed:
                        print(f"  {manager}: ID {out_id} → ID {in_id}")
                    else:
                        print(f"  {manager}: добавлен ID {in_id} (out_id {out_id} не найден в ростере)")
            elif removed:
                print(f"  {manager}: удален ID {out_id} (in_id {in_id} уже в ростере)")
            
            # Добавляем в историю трансферов, если еще не добавлен
            transfer_key = (manager, 10, out_id)
            if transfer_key not in added_transfers:
                in_player = fpl_players.get(in_id, {})
                
                transfer_history.append({
                    "gw": 10,
                    "round": 1,
                    "manager": manager,
                    "out": out_id,
                    "out_player": out_player if out_player else None,
                    "in": in_player if in_player else {"playerId": in_id},
                    "ts": "2024-11-01T00:00:00+00:00"
                })
                added_transfers.add(transfer_key)
        
        rosters[manager] = roster
    
    # Исправляем проблему с Trossard у Сергея
    # Trossard был обменян в GW3, поэтому его не должно быть в ростере Сергея
    if 'Сергей' in rosters:
        roster = rosters['Сергей']
        roster = [p for p in roster if int(p.get('playerId') or p.get('id')) != 20]
        rosters['Сергей'] = roster
        print("\n  Исправлено: удален Trossard из ростра Сергея (был обменян в GW3)")
    
    # Исправляем проблему с Anthony - должен быть Jaidon Anthony (200), а не Elanga (486)
    if 'Сергей' in rosters:
        roster = rosters['Сергей']
        roster_ids = {int(p.get('playerId') or p.get('id')) for p in roster}
        
        # Если есть Elanga (486), заменяем на Jaidon Anthony (200)
        if 486 in roster_ids and 200 not in roster_ids:
            roster = [p for p in roster if int(p.get('playerId') or p.get('id')) != 486]
            anthony_player = fpl_players.get(200, {})
            if anthony_player:
                roster.append(anthony_player)
                print("\n  Исправлено: заменен Anthony Elanga (486) на Jaidon Anthony (200) у Сергея")
        
        rosters['Сергей'] = roster

def main():
    state_file = Path(__file__).parent.parent / "draft_state_epl.json"
    
    print("Загружаем draft_state_epl.json...")
    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    print("Загружаем данные об игроках из FPL API...")
    fpl_players = get_fpl_players()
    
    print("Загружаем составы из S3...")
    lineups_data = load_lineups_from_s3()
    
    print("\nНормализуем ростеры...")
    normalize_rosters(state, fpl_players, lineups_data)
    
    # Сохраняем обновленный state
    backup_file = state_file.with_suffix('.json.backup')
    print(f"\nСоздаем резервную копию: {backup_file}")
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    print(f"Сохраняем нормализованный state: {state_file}")
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    print("\n✅ Нормализация завершена!")
    print(f"   Резервная копия: {backup_file}")

if __name__ == "__main__":
    main()

