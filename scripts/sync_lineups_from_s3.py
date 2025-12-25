#!/usr/bin/env python3
"""
Скрипт для синхронизации составов из AWS S3
Загружает составы из S3, фильтрует некорректные ID и дополняет до 11 игроков
"""
import json
import sys
import os
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3
from botocore.exceptions import ClientError, BotoCoreError
from draft_app.lineup_store import save_lineup, _slug_parts, S3_PREFIX
from draft_app.epl_services import get_roster_for_gw, load_state
from draft_app.config import EPL_USERS

def list_lineups_from_s3(bucket: str, prefix: str = "lineups") -> dict:
    """Получает список всех составов из S3"""
    try:
        s3_client = boto3.client("s3")
        lineups = {}
        
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
                        lineups.setdefault(user_slug, {})[gw] = key
                    except (ValueError, IndexError):
                        continue
        
        return lineups
    except Exception as e:
        print(f"❌ Ошибка при получении списка из S3: {e}")
        return {}

def get_manager_from_slug(slug: str, managers: list) -> str:
    """Определяет менеджера по slug"""
    for manager in managers:
        manager_slug, _, _ = _slug_parts(manager)
        if manager_slug == slug:
            return manager
    return None

def sync_lineups_from_s3(bucket: str, prefix: str = "lineups", dry_run: bool = False):
    """Синхронизирует составы из S3"""
    if not bucket:
        print("❌ S3_BUCKET не указан")
        return
    
    print(f"📦 Загружаем составы из S3: s3://{bucket}/{prefix}/")
    
    # Получаем список составов из S3
    s3_lineups = list_lineups_from_s3(bucket, prefix)
    
    if not s3_lineups:
        print("⚠️  Составы в S3 не найдены")
        return
    
    print(f"📋 Найдено {sum(len(gws) for gws in s3_lineups.values())} составов в S3")
    
    # Загружаем текущий state для проверки трансферов
    state = load_state()
    managers = [m for m in EPL_USERS if m in state.get("rosters", {})]
    
    s3_client = boto3.client("s3")
    max_valid_id = 1000
    synced_count = 0
    filtered_count = 0
    
    for user_slug, gws in s3_lineups.items():
        manager = get_manager_from_slug(user_slug, managers)
        if not manager:
            print(f"⚠️  Не найден менеджер для slug: {user_slug}")
            continue
        
        for gw, key in sorted(gws.items()):
            try:
                # Загружаем состав из S3
                obj = s3_client.get_object(Bucket=bucket, Key=key)
                body = obj.get("Body").read().decode("utf-8")
                lineup_data = json.loads(body)
                
                if not isinstance(lineup_data, dict):
                    continue
                
                # Получаем ростер для этого GW с учетом трансферов
                roster_for_gw = get_roster_for_gw(state, manager, gw)
                valid_player_ids = {int(p.get("playerId") or p.get("id")) for p in roster_for_gw}
                
                original_players = lineup_data.get("players", [])
                original_bench = lineup_data.get("bench", [])
                
                # Фильтруем некорректные ID и игроков, которых нет в ростере
                valid_players = [
                    pid for pid in original_players 
                    if isinstance(pid, int) and pid in valid_player_ids and 1 <= pid <= max_valid_id
                ]
                valid_bench = [
                    pid for pid in original_bench 
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
                
                # Проверяем, нужно ли обновление
                needs_update = (
                    len(valid_players) != len(original_players) or 
                    len(valid_bench) != len(original_bench) or
                    set(valid_players) != set(original_players) or
                    set(valid_bench) != set(original_bench)
                )
                
                if needs_update:
                    filtered_count += 1
                    updated_lineup = {
                        "formation": lineup_data.get("formation", "4-4-2"),
                        "players": valid_players,
                        "bench": valid_bench,
                        "ts": lineup_data.get("ts"),
                    }
                    
                    if not dry_run:
                        save_lineup(manager, gw, updated_lineup)
                    print(f"  {'[DRY RUN] ' if dry_run else ''}⚠️  {manager} GW{gw}: отфильтровано/дополнено → {len(valid_players)} в старте, {len(valid_bench)} на скамейке")
                else:
                    if not dry_run:
                        # Сохраняем как есть, чтобы обновить локальный кэш
                        save_lineup(manager, gw, lineup_data)
                    print(f"  {'[DRY RUN] ' if dry_run else ''}✓ {manager} GW{gw}: {len(valid_players)} в старте, {len(valid_bench)} на скамейке")
                
                synced_count += 1
                
            except (ClientError, BotoCoreError) as e:
                print(f"  ❌ Ошибка загрузки {key}: {e}")
            except Exception as e:
                print(f"  ❌ Ошибка обработки {key}: {e}")
    
    print(f"\n✅ Синхронизировано составов: {synced_count}")
    if filtered_count > 0:
        print(f"⚠️  Отфильтровано/дополнено: {filtered_count}")
    if dry_run:
        print("\n💡 Это был dry-run. Запустите без --dry-run для реальной синхронизации.")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Синхронизация составов из AWS S3")
    parser.add_argument("--bucket", help="S3 bucket name", default=os.getenv("LINEUP_S3_BUCKET") or os.getenv("DRAFT_S3_BUCKET"))
    parser.add_argument("--prefix", help="S3 prefix", default=os.getenv("LINEUP_S3_PREFIX") or os.getenv("DRAFT_S3_LINEUPS_PREFIX", "lineups"))
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет сделано, без реальных изменений")
    
    args = parser.parse_args()
    
    if not args.bucket:
        print("❌ Укажите S3 bucket через --bucket или переменную окружения LINEUP_S3_BUCKET/DRAFT_S3_BUCKET")
        sys.exit(1)
    
    sync_lineups_from_s3(args.bucket, args.prefix, dry_run=args.dry_run)

