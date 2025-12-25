#!/usr/bin/env python3
"""
Исправление схем в составах на основе фактического распределения игроков
"""
import json
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from draft_app.lineup_store import _slug_parts, save_lineup
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
                    players_dict[pid] = {
                        'position': pos
                    }
            return players_dict
    except Exception as e:
        print(f'Ошибка загрузки FPL API: {e}')
        return {}

def detect_formation(players: List[int], fpl_players: dict) -> str:
    """Определяет схему на основе фактического распределения игроков"""
    gk = 0
    def_count = 0
    mid_count = 0
    fwd_count = 0
    
    for pid in players:
        if 1 <= pid <= 1000:
            player_info = fpl_players.get(pid, {})
            pos = player_info.get('position', '?')
            if pos == 'Goalkeeper':
                gk += 1
            elif pos == 'Defender':
                def_count += 1
            elif pos == 'Midfielder':
                mid_count += 1
            elif pos == 'Forward':
                fwd_count += 1
    
    # Определяем схему
    if gk == 1:
        # Стандартные схемы
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
        elif def_count == 4 and mid_count == 2 and fwd_count == 3:
            return "4-2-3-1"
        elif def_count == 4 and mid_count == 1 and fwd_count == 4:
            return "4-1-4-1"
        else:
            # Возвращаем фактическое распределение
            return f"{def_count}-{mid_count}-{fwd_count}"
    else:
        # Нет вратаря или больше одного
        return f"{def_count}-{mid_count}-{fwd_count}"

def load_lineup_from_s3(manager: str, gw: int, bucket: str = "val-draft-storage", prefix: str = "lineups") -> dict:
    """Загружает состав из S3"""
    slug, _, _ = _slug_parts(manager)
    regions = ["us-east-1", "eu-central-1", "eu-west-1"]
    base_urls = [f"https://{bucket}.s3.{region}.amazonaws.com" for region in regions]
    
    for base_url in base_urls:
        url = f"{base_url}/{prefix}/{slug}/gw{gw}.json"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception:
            continue
    return None

def fix_lineup_formation(manager: str, gw: int, fpl_players: dict):
    """Исправляет схему в составе"""
    lineup = load_lineup_from_s3(manager, gw)
    if not lineup:
        return False
    
    players = lineup.get('players', [])
    valid_players = [p for p in players if 1 <= p <= 1000]
    
    if len(valid_players) != 11:
        return False
    
    current_formation = lineup.get('formation', '')
    detected_formation = detect_formation(valid_players, fpl_players)
    
    if current_formation != detected_formation:
        lineup['formation'] = detected_formation
        save_lineup(manager, gw, lineup)
        return True
    
    return False

def main():
    print("=" * 80)
    print("ИСПРАВЛЕНИЕ СХЕМ В СОСТАВАХ")
    print("=" * 80)
    print()
    
    # Загружаем данные
    print("Загружаем данные об игроках из FPL API...")
    fpl_players = get_fpl_players()
    
    # Проблемные составы из проверки
    issues = [
        ('Сергей', 11), ('Сергей', 12), ('Сергей', 13), ('Сергей', 14), ('Сергей', 15), ('Сергей', 16),
        ('Тёма', 8), ('Тёма', 9), ('Тёма', 10),
        ('Тёма', 11), ('Тёма', 12), ('Тёма', 13), ('Тёма', 14), ('Тёма', 15), ('Тёма', 16), ('Тёма', 17),
    ]
    
    print(f"\nИсправляем {len(issues)} составов...")
    print()
    
    fixed_count = 0
    for manager, gw in issues:
        lineup = load_lineup_from_s3(manager, gw)
        if not lineup:
            print(f"  ⚠️  {manager} GW{gw}: состав не найден")
            continue
        
        players = lineup.get('players', [])
        valid_players = [p for p in players if 1 <= p <= 1000]
        
        if len(valid_players) != 11:
            print(f"  ⚠️  {manager} GW{gw}: неполный состав ({len(valid_players)} игроков)")
            continue
        
        current_formation = lineup.get('formation', '')
        detected_formation = detect_formation(valid_players, fpl_players)
        
        if current_formation != detected_formation:
            print(f"  ✅ {manager} GW{gw}: {current_formation} → {detected_formation}")
            lineup['formation'] = detected_formation
            save_lineup(manager, gw, lineup)
            fixed_count += 1
        else:
            print(f"  ℹ️  {manager} GW{gw}: схема уже правильная ({current_formation})")
    
    print()
    print("=" * 80)
    print(f"✅ Исправлено схем: {fixed_count}")

if __name__ == "__main__":
    main()

