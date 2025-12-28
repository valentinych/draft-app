#!/usr/bin/env python3
"""
Скачивание статистики всех игроков UCL локально.
Скачивает popupstats для всех игроков из players_80_en_1.json
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
import requests

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.ucl import (
    _json_load,
    _players_from_ucl,
    UCL_PLAYERS,
)

POPUPSTATS_DIR = BASE_DIR / "popupstats"
POPUPSTATS_DIR.mkdir(exist_ok=True)

def download_player_stats(pid: int, retries: int = 3) -> Dict[str, Any] | None:
    """Download player stats from UEFA API"""
    url = f"https://gaming.uefa.com/en/uclfantasy/services/feeds/popupstats/popupstats_80_{pid}.json"
    local_path = POPUPSTATS_DIR / f"popupstats_80_{pid}.json"
    
    # If file exists and is recent (less than 1 hour old), skip
    if local_path.exists():
        stat = local_path.stat()
        age_seconds = time.time() - stat.st_mtime
        if age_seconds < 3600:  # 1 hour
            try:
                with open(local_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Save to local file
            with open(local_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            return data
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            print(f"  ❌ Ошибка для игрока {pid}: {e}")
            return None
        except Exception as e:
            print(f"  ❌ Неожиданная ошибка для игрока {pid}: {e}")
            return None
    
    return None

def main():
    print("=" * 80)
    print("СКАЧИВАНИЕ СТАТИСТИКИ ИГРОКОВ UCL")
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
    print(f"📁 Сохранение в: {POPUPSTATS_DIR}")
    print(f"\n📥 Начинаю скачивание...")
    
    downloaded = 0
    skipped = 0
    errors = 0
    
    for i, pid in enumerate(player_ids, 1):
        if i % 50 == 0:
            print(f"  Прогресс: {i}/{len(player_ids)} (скачано: {downloaded}, пропущено: {skipped}, ошибок: {errors})")
        
        local_path = POPUPSTATS_DIR / f"popupstats_80_{pid}.json"
        
        # Check if already exists and recent
        if local_path.exists():
            stat = local_path.stat()
            age_seconds = time.time() - stat.st_mtime
            if age_seconds < 3600:  # 1 hour
                skipped += 1
                continue
        
        # Download
        result = download_player_stats(pid)
        if result:
            downloaded += 1
        else:
            errors += 1
        
        # Small delay to avoid rate limiting
        time.sleep(0.1)
    
    print(f"\n✅ Завершено!")
    print(f"   Скачано: {downloaded}")
    print(f"   Пропущено (уже есть): {skipped}")
    print(f"   Ошибок: {errors}")
    print("=" * 80)

if __name__ == "__main__":
    main()

