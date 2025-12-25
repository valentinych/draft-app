#!/usr/bin/env python3
"""
Анализ трансферов на основе lineups из AWS S3
Сравнивает составы по GW, чтобы найти, когда игроки появились/исчезли
"""
import json
import sys
import os
from pathlib import Path
from typing import Dict, List, Set, Optional
from collections import defaultdict
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3
from botocore.exceptions import ClientError, BotoCoreError
from draft_app.lineup_store import _slug_parts, S3_PREFIX, S3_BUCKET
from draft_app.config import EPL_USERS

def get_s3_client():
    """Получает S3 клиент"""
    try:
        return boto3.client("s3")
    except Exception as e:
        print(f"Ошибка создания S3 клиента: {e}")
        return None

def load_lineup_from_url(url: str) -> Optional[dict]:
    """Загружает состав из публичного URL"""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except Exception as e:
        return None

def list_all_lineups_from_s3(bucket: str, prefix: str = "lineups", use_public_url: bool = True) -> Dict[str, Dict[int, dict]]:
    """Получает все составы из S3, сгруппированные по менеджеру и GW"""
    lineups_by_manager = defaultdict(dict)
    
    if use_public_url:
        # Используем публичные URL для загрузки
        # Формат: https://{bucket}.s3.{region}.amazonaws.com/{prefix}/user_xxx/gwN.json
        # Пробуем разные регионы
        regions = ["us-east-1", "eu-central-1", "eu-west-1"]
        base_urls = [f"https://{bucket}.s3.{region}.amazonaws.com" for region in regions]
        
        # Получаем список всех менеджеров и их slugs
        managers = EPL_USERS
        manager_slugs = {}
        for manager in managers:
            slug, _, _ = _slug_parts(manager)
            manager_slugs[slug] = manager
        
        # Пробуем загрузить составы для каждого менеджера и GW
        print("Загружаем составы из публичных URL...")
        loaded_count = 0
        
        for slug, manager in manager_slugs.items():
            # Пробуем GW от 1 до 20
            for gw in range(1, 21):
                for base_url in base_urls:
                    url = f"{base_url}/{prefix}/{slug}/gw{gw}.json"
                    lineup_data = load_lineup_from_url(url)
                    if lineup_data:
                        lineups_by_manager[slug][gw] = lineup_data
                        loaded_count += 1
                        break  # Если загрузили, переходим к следующему GW
        
        print(f"Загружено составов: {loaded_count}")
    else:
        # Используем S3 API (требует credentials)
        s3_client = get_s3_client()
        if not s3_client:
            return {}
        
        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
            
            for page in pages:
                if 'Contents' not in page:
                    continue
                for obj in page['Contents']:
                    key = obj['Key']
                    # Парсим путь: lineups/user_xxx/gwN.json
                    parts = key.split('/')
                    if len(parts) >= 3 and parts[-1].startswith('gw') and parts[-1].endswith('.json'):
                        try:
                            gw = int(parts[-1][2:-5])  # Извлекаем число из "gw17.json"
                            user_slug = parts[-2]  # user_xxx
                            
                            # Загружаем состав
                            try:
                                obj_data = s3_client.get_object(Bucket=bucket, Key=key)
                                body = obj_data.get("Body").read().decode("utf-8")
                                lineup_data = json.loads(body)
                                lineups_by_manager[user_slug][gw] = lineup_data
                            except Exception as e:
                                print(f"Ошибка загрузки {key}: {e}")
                        except (ValueError, IndexError):
                            continue
        except Exception as e:
            print(f"Ошибка при получении списка из S3: {e}")
    
    return lineups_by_manager

def get_manager_from_slug(slug: str, managers: List[str]) -> Optional[str]:
    """Определяет менеджера по slug"""
    for manager in managers:
        manager_slug, _, _ = _slug_parts(manager)
        if manager_slug == slug:
            return manager
    return None

def get_players_from_lineup(lineup: dict) -> Set[int]:
    """Извлекает все ID игроков из состава (старт + скамейка)"""
    players = set()
    if isinstance(lineup, dict):
        for pid in lineup.get('players', []):
            if isinstance(pid, int):
                players.add(pid)
        for pid in lineup.get('bench', []):
            if isinstance(pid, int):
                players.add(pid)
    return players

def analyze_transfers_from_lineups(lineups_by_manager: Dict[str, Dict[int, dict]], 
                                   original_rosters: Dict[str, Set[int]],
                                   managers: List[str]) -> Dict[str, Dict[int, List[dict]]]:
    """Анализирует трансферы на основе изменений в составах"""
    transfers = defaultdict(lambda: defaultdict(list))
    
    for user_slug, gw_lineups in lineups_by_manager.items():
        manager = get_manager_from_slug(user_slug, managers)
        if not manager:
            continue
        
        original_players = original_rosters.get(manager, set())
        sorted_gws = sorted(gw_lineups.keys())
        
        # Отслеживаем игроков по GW
        players_by_gw = {}
        for gw in sorted_gws:
            lineup = gw_lineups[gw]
            players_by_gw[gw] = get_players_from_lineup(lineup)
        
        # Находим трансферы после GW3
        # Сравниваем оригинальный ростер с ростером после GW3
        # Игроки, которые были в оригинале, но исчезли после GW3
        # Игроки, которые появились после GW3, но не были в оригинале
        
        # Определяем ростер после GW3 (берем первый доступный GW после 3)
        gw_after_3 = None
        for gw in sorted_gws:
            if gw > 3:
                gw_after_3 = gw
                break
        
        gw3_out = set()
        gw3_in = set()
        
        if gw_after_3:
            players_after_gw3 = players_by_gw[gw_after_3]
            # Игроки, которые были в оригинале, но не в составе после GW3
            gw3_out = original_players - players_after_gw3
            # Игроки, которые появились после GW3, но не были в оригинале
            gw3_in = players_after_gw3 - original_players
        
        # Находим трансферы после GW10
        # Сравниваем ростер GW10 с ростером GW11
        gw10_out = set()
        gw10_in = set()
        
        if 10 in players_by_gw and 11 in players_by_gw:
            gw10_players = players_by_gw[10]
            gw11_players = players_by_gw[11]
            
            # Игроки, которые были в GW10, но исчезли к GW11
            gw10_out = gw10_players - gw11_players
            # Игроки, которые появились в GW11, но не были в GW10
            gw10_in = gw11_players - gw10_players
        
        # Сохраняем трансферы
        if gw3_out or gw3_in:
            out_list = sorted(list(gw3_out))
            in_list = sorted(list(gw3_in))
            # Сопоставляем 1:1
            for i in range(min(len(out_list), len(in_list))):
                transfers[manager][3].append({
                    'out': out_list[i],
                    'in': in_list[i]
                })
        
        if gw10_out or gw10_in:
            out_list = sorted(list(gw10_out))
            in_list = sorted(list(gw10_in))
            # Сопоставляем 1:1
            for i in range(min(len(out_list), len(in_list))):
                transfers[manager][10].append({
                    'out': out_list[i],
                    'in': in_list[i]
                })
    
    return transfers

def get_player_info_from_state(state: dict, player_id: int) -> Optional[dict]:
    """Получает информацию об игроке из state"""
    # Ищем в рострах всех менеджеров
    rosters = state.get('rosters', {})
    for manager, roster in rosters.items():
        for player in roster:
            pid = player.get('playerId') or player.get('id')
            if pid == player_id:
                return player
    
    # Ищем в picks
    picks = state.get('picks', [])
    for pick in picks:
        player = pick.get('player', {})
        if player:
            pid = player.get('playerId') or player.get('id')
            if pid == player_id:
                return player
    
    return None

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Анализ трансферов на основе lineups из AWS S3")
    parser.add_argument("--bucket", help="S3 bucket name", default=S3_BUCKET or os.getenv("DRAFT_S3_BUCKET", "val-draft-storage"))
    parser.add_argument("--prefix", help="S3 prefix", default=S3_PREFIX or os.getenv("DRAFT_S3_LINEUPS_PREFIX", "lineups"))
    parser.add_argument("--use-public-url", action="store_true", default=True, help="Использовать публичные URL вместо S3 API")
    parser.add_argument("--no-public-url", dest="use_public_url", action="store_false", help="Использовать S3 API (требует credentials)")
    
    args = parser.parse_args()
    bucket = args.bucket
    prefix = args.prefix
    use_public_url = args.use_public_url
    
    if not bucket:
        print("❌ S3_BUCKET не указан")
        print("   Укажите через --bucket или переменную окружения DRAFT_S3_BUCKET или LINEUP_S3_BUCKET")
        return
    
    if use_public_url:
        print(f"📦 Загружаем составы из публичных URL: https://{bucket}.s3.*.amazonaws.com/{prefix}/")
    else:
        print(f"📦 Загружаем составы из S3: s3://{bucket}/{prefix}/")
    print()
    
    # Загружаем текущий state для получения оригинальных ростеров и информации об игроках
    state_file = Path(__file__).parent.parent / "draft_state_epl.json"
    if not state_file.exists():
        print(f"❌ Файл {state_file} не найден")
        return
    
    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    # Получаем оригинальные ростеры из picks
    original_rosters = {}
    picks = state.get('picks', [])
    for pick in picks:
        manager = pick.get('user')
        if not manager:
            continue
        if manager not in original_rosters:
            original_rosters[manager] = set()
        player = pick.get('player')
        if player:
            pid = player.get('playerId') or player.get('id')
            if pid:
                original_rosters[manager].add(int(pid))
    
    # Загружаем составы из S3
    lineups_by_manager = list_all_lineups_from_s3(bucket, prefix, use_public_url=use_public_url)
    
    if not lineups_by_manager:
        print("⚠️  Составы в S3 не найдены")
        return
    
    print(f"📋 Найдено составов в S3: {sum(len(gws) for gws in lineups_by_manager.values())}")
    print()
    
    # Анализируем трансферы
    managers = [m for m in EPL_USERS if m in original_rosters]
    transfers = analyze_transfers_from_lineups(lineups_by_manager, original_rosters, managers)
    
    # Выводим результаты
    print('=' * 80)
    print('ТРАНСФЕРЫ, ОПРЕДЕЛЕННЫЕ ИЗ S3 LINEUPS')
    print('=' * 80)
    print()
    
    print('ТРАНСФЕРЫ ПОСЛЕ GW3:')
    print('-' * 80)
    gw3_count = 0
    for manager in sorted(transfers.keys()):
        if 3 in transfers[manager]:
            print(f'\n{manager}:')
            for t in transfers[manager][3]:
                out_id = t['out']
                in_id = t['in']
                out_player = get_player_info_from_state(state, out_id)
                in_player = get_player_info_from_state(state, in_id)
                out_name = out_player.get('fullName', f'ID {out_id}') if out_player else f'ID {out_id}'
                in_name = in_player.get('fullName', f'ID {in_id}') if in_player else f'ID {in_id}'
                out_pos = out_player.get('position', '?') if out_player else '?'
                in_pos = in_player.get('position', '?') if in_player else '?'
                print(f'  {out_name} ({out_pos}, ID: {out_id}) → {in_name} ({in_pos}, ID: {in_id})')
                gw3_count += 1
        else:
            print(f'\n{manager}: трансферов после GW3 не найдено')
    
    print()
    print('=' * 80)
    print('ТРАНСФЕРЫ ПОСЛЕ GW10:')
    print('-' * 80)
    gw10_count = 0
    for manager in sorted(transfers.keys()):
        if 10 in transfers[manager]:
            print(f'\n{manager}:')
            for t in transfers[manager][10]:
                out_id = t['out']
                in_id = t['in']
                out_player = get_player_info_from_state(state, out_id)
                in_player = get_player_info_from_state(state, in_id)
                out_name = out_player.get('fullName', f'ID {out_id}') if out_player else f'ID {out_id}'
                in_name = in_player.get('fullName', f'ID {in_id}') if in_player else f'ID {in_id}'
                out_pos = out_player.get('position', '?') if out_player else '?'
                in_pos = in_player.get('position', '?') if in_player else '?'
                print(f'  {out_name} ({out_pos}, ID: {out_id}) → {in_name} ({in_pos}, ID: {in_id})')
                gw10_count += 1
        else:
            print(f'\n{manager}: трансферов после GW10 не найдено')
    
    print()
    print('=' * 80)
    print(f'ИТОГО: GW3 - {gw3_count} трансферов, GW10 - {gw10_count} трансферов')

if __name__ == "__main__":
    main()

