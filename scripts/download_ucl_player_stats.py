#!/usr/bin/env python3
"""
Скачивание статистики всех игроков UCL и сохранение в S3.
Использует функции из ucl_stats_store для сохранения в S3 (ucl/popupstats_80_{pid}.json)

Использование:
  python3 scripts/download_ucl_player_stats.py          # Проверяет кеш, пропускает существующие
  python3 scripts/download_ucl_player_stats.py --force  # Принудительно обновляет все файлы
"""
from __future__ import annotations
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.ucl import (
    _json_load,
    _players_from_ucl,
    UCL_PLAYERS,
)
from draft_app.ucl_stats_store import (
    stats_s3_key,
    stats_bucket,
)
# Import private functions directly from module
from draft_app import ucl_stats_store
from datetime import datetime

def main():
    # Check for --force flag
    force_refresh = "--force" in sys.argv or "-f" in sys.argv
    
    print("=" * 80)
    print("СКАЧИВАНИЕ СТАТИСТИКИ ИГРОКОВ UCL В S3")
    if force_refresh:
        print("⚠️  РЕЖИМ ПРИНУДИТЕЛЬНОГО ОБНОВЛЕНИЯ (игнорирует кеш)")
    print("=" * 80)
    
    # Load players
    raw_players = _json_load(UCL_PLAYERS)
    if not raw_players:
        print("❌ Ошибка: не удалось загрузить players_80_en_1.json")
        print(f"   Путь: {UCL_PLAYERS}")
        return
    
    all_players = _players_from_ucl(raw_players)
    print(f"✅ Загружено игроков: {len(all_players)}")
    
    # Get all player IDs
    player_ids = []
    for player in all_players:
        pid = player.get("playerId")
        if pid:
            try:
                player_ids.append(int(pid))
            except Exception:
                pass
    
    print(f"✅ Найдено ID игроков: {len(player_ids)}")
    bucket = stats_bucket()
    print(f"📦 S3 Bucket: {bucket}")
    print(f"📁 S3 Prefix: ucl/")
    if force_refresh:
        print(f"\n📥 Начинаю ПРИНУДИТЕЛЬНОЕ скачивание и загрузку в S3...")
    else:
        print(f"\n📥 Начинаю скачивание и загрузку в S3 (проверка кеша включена)...")
    
    downloaded = 0
    skipped = 0
    errors = 0
    
    for i, pid in enumerate(player_ids, 1):
        if i % 50 == 0:
            print(f"  Прогресс: {i}/{len(player_ids)} (скачано: {downloaded}, пропущено: {skipped}, ошибок: {errors})", flush=True)
        
        try:
            # Check S3 cache only if not forcing refresh
            if not force_refresh:
                s3_payload = ucl_stats_store._load_s3(pid)
                if ucl_stats_store._fresh(s3_payload):
                    # Already in S3, skip
                    skipped += 1
                    continue
            
            # Download from remote (always if force_refresh, or if not in cache)
            remote = ucl_stats_store._fetch_remote_player(pid)
            if remote is not None:
                payload = {
                    "cached_at": datetime.utcnow().isoformat(),
                    "data": remote,
                }
                # Save directly to S3 (no local save needed on Heroku)
                ucl_stats_store._save_s3(pid, payload)
                downloaded += 1
            else:
                # Failed to download
                errors += 1
                print(f"  ⚠️  Не удалось скачать данные для игрока {pid}", flush=True)
            
        except KeyboardInterrupt:
            print(f"\n⚠️  Прервано пользователем на игроке {pid}", flush=True)
            raise
        except Exception as e:
            errors += 1
            print(f"  ❌ Ошибка для игрока {pid}: {e}", flush=True)
            # Longer delay after error to avoid rate limiting
            time.sleep(2)
        
        # Small delay to avoid rate limiting
        time.sleep(0.3)
    
    print(f"\n✅ Завершено!")
    print(f"   Скачано и загружено в S3: {downloaded}")
    print(f"   Пропущено (уже в кеше): {skipped}")
    print(f"   Ошибок: {errors}")
    print(f"   S3 Bucket: {bucket}")
    print(f"   S3 Prefix: ucl/")
    print(f"   S3 Path: s3://{bucket}/ucl/popupstats_80_*.json")
    print("=" * 80)

if __name__ == "__main__":
    main()
