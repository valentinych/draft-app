#!/usr/bin/env python3
"""
Update Top-4 players from API Football
This script fetches fresh player data from API Football and updates the cache

Usage:
    python3 scripts/update_top4_players_api_football.py
    or
    heroku run --app val-draft-app "python3 scripts/update_top4_players_api_football.py"
"""
import sys
import os
from pathlib import Path

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.top4_services import (
    load_players,
    _fetch_players_from_api_football,
    PLAYERS_CACHE,
    _json_dump_atomic,
    _s3_enabled,
    _s3_bucket,
    _s3_players_key,
    _s3_put_json,
)

def main():
    print("=" * 80)
    print("ОБНОВЛЕНИЕ ДАННЫХ ИГРОКОВ TOP-4 ИЗ API FOOTBALL")
    print("=" * 80)
    
    # Set environment variable to use API Football
    os.environ["TOP4_USE_API_FOOTBALL"] = "true"
    
    print("\n📥 Загрузка игроков из API Football...")
    players = _fetch_players_from_api_football()
    
    if not players:
        print("❌ Не удалось загрузить игроков из API Football")
        return 1
    
    print(f"\n✅ Загружено игроков: {len(players)}")
    
    # Show sample
    if players:
        print("\nПримеры игроков:")
        for i, p in enumerate(players[:5]):
            print(f"  {i+1}. {p.get('fullName')} ({p.get('position')}) - {p.get('clubName')} ({p.get('league')})")
    
    # Save to local cache
    print(f"\n💾 Сохранение в локальный кеш: {PLAYERS_CACHE}")
    _json_dump_atomic(PLAYERS_CACHE, players)
    
    # Save to S3 if enabled
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_players_key()
        if bucket and key:
            print(f"💾 Сохранение в S3: s3://{bucket}/{key}")
            if _s3_put_json(bucket, key, players):
                print("✅ Данные успешно сохранены в S3")
            else:
                print("⚠️  Не удалось сохранить в S3")
    
    print("\n✅ Обновление завершено!")
    print("=" * 80)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

