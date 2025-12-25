#!/usr/bin/env python3
"""
Проверка соответствия составов схемам (формациям)
"""
import json
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from draft_app.lineup_store import _slug_parts
from draft_app.config import EPL_USERS

# Валидные схемы (формации)
VALID_FORMATIONS = {
    "3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1",
    "5-3-2", "5-4-1", "3-4-2-1", "4-2-3-1", "4-1-4-1"
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
                    players_dict[pid] = {
                        'position': pos
                    }
            return players_dict
    except Exception as e:
        print(f'Ошибка загрузки FPL API: {e}')
        return {}

def parse_formation(formation: str) -> Tuple[int, int, int]:
    """Парсит схему формации (например, "4-4-2") в (DEF, MID, FWD)"""
    if not formation or not isinstance(formation, str):
        return None
    
    parts = formation.split('-')
    if len(parts) >= 3:
        try:
            def_count = int(parts[0])
            mid_count = int(parts[1])
            fwd_count = int(parts[2])
            return (def_count, mid_count, fwd_count)
        except ValueError:
            return None
    return None

def validate_formation(formation: str, players: List[int], bench: List[int], fpl_players: dict) -> Tuple[bool, str]:
    """Проверяет, соответствует ли состав схеме"""
    if formation not in VALID_FORMATIONS:
        return False, f"Неизвестная схема: {formation}"
    
    formation_counts = parse_formation(formation)
    if not formation_counts:
        return False, f"Некорректный формат схемы: {formation}"
    
    expected_def, expected_mid, expected_fwd = formation_counts
    expected_gk = 1
    
    # Считаем игроков по позициям в старте
    actual_gk = 0
    actual_def = 0
    actual_mid = 0
    actual_fwd = 0
    
    for pid in players:
        if 1 <= pid <= 1000:  # Фильтруем некорректные ID
            player_info = fpl_players.get(pid, {})
            pos = player_info.get('position', '?')
            if pos == 'Goalkeeper':
                actual_gk += 1
            elif pos == 'Defender':
                actual_def += 1
            elif pos == 'Midfielder':
                actual_mid += 1
            elif pos == 'Forward':
                actual_fwd += 1
    
    # Проверяем соответствие
    issues = []
    if actual_gk != expected_gk:
        issues.append(f"GK: {actual_gk} вместо {expected_gk}")
    if actual_def != expected_def:
        issues.append(f"DEF: {actual_def} вместо {expected_def}")
    if actual_mid != expected_mid:
        issues.append(f"MID: {actual_mid} вместо {expected_mid}")
    if actual_fwd != expected_fwd:
        issues.append(f"FWD: {actual_fwd} вместо {expected_fwd}")
    
    if issues:
        return False, ", ".join(issues)
    
    return True, "OK"

def load_all_lineups_from_s3(bucket: str = "val-draft-storage", prefix: str = "lineups") -> Dict[str, Dict[int, dict]]:
    """Загружает все составы из S3"""
    lineups_by_manager = defaultdict(dict)
    
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
                        lineups_by_manager[manager][gw] = lineup
                        loaded_count += 1
                        break
                except Exception:
                    continue
    
    print(f"Загружено составов: {loaded_count}")
    return lineups_by_manager

def main():
    print("=" * 80)
    print("ПРОВЕРКА СООТВЕТСТВИЯ СОСТАВОВ СХЕМАМ")
    print("=" * 80)
    print()
    
    # Загружаем данные
    print("Загружаем данные об игроках из FPL API...")
    fpl_players = get_fpl_players()
    
    print("Загружаем составы из S3...")
    lineups_data = load_all_lineups_from_s3()
    
    print()
    print("=" * 80)
    print("РЕЗУЛЬТАТЫ ПРОВЕРКИ:")
    print("=" * 80)
    print()
    
    total_lineups = 0
    valid_lineups = 0
    invalid_lineups = []
    missing_formations = []
    
    for manager in sorted(lineups_data.keys()):
        manager_lineups = lineups_data[manager]
        print(f"{manager}:")
        
        for gw in sorted(manager_lineups.keys()):
            lineup = manager_lineups[gw]
            total_lineups += 1
            
            formation = lineup.get('formation', '')
            players = lineup.get('players', [])
            bench = lineup.get('bench', [])
            
            # Фильтруем некорректные ID
            valid_players = [p for p in players if 1 <= p <= 1000]
            
            if not formation:
                missing_formations.append((manager, gw))
                print(f"  GW{gw}: ⚠️  схема не указана")
                continue
            
            is_valid, message = validate_formation(formation, valid_players, bench, fpl_players)
            
            if is_valid:
                valid_lineups += 1
                print(f"  GW{gw}: ✅ {formation} - {message}")
            else:
                invalid_lineups.append((manager, gw, formation, message))
                print(f"  GW{gw}: ❌ {formation} - {message}")
        
        print()
    
    print("=" * 80)
    print("ИТОГОВАЯ СТАТИСТИКА:")
    print("=" * 80)
    print(f"Всего составов: {total_lineups}")
    print(f"Валидных: {valid_lineups} ({valid_lineups*100//total_lineups if total_lineups > 0 else 0}%)")
    print(f"Невалидных: {len(invalid_lineups)}")
    print(f"Без схемы: {len(missing_formations)}")
    
    if invalid_lineups:
        print()
        print("НЕВАЛИДНЫЕ СОСТАВЫ:")
        print("-" * 80)
        for manager, gw, formation, message in invalid_lineups:
            print(f"{manager} GW{gw}: схема {formation} - {message}")
    
    if missing_formations:
        print()
        print("СОСТАВЫ БЕЗ СХЕМЫ:")
        print("-" * 80)
        for manager, gw in missing_formations:
            print(f"{manager} GW{gw}")

if __name__ == "__main__":
    main()

