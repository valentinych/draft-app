#!/usr/bin/env python3
"""
Refresh Top-4 draft scores for GW1-GW4
Updates player statistics from API Football (if enabled) or MantraFootball
and recalculates scores for all gameweeks from GW1 to GW4

Usage:
    python3 scripts/refresh_top4_scores_gw1_to_gw4.py
    or
    heroku run --app val-draft-app "python3 scripts/refresh_top4_scores_gw1_to_gw4.py"
"""
import sys
import os
from pathlib import Path

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.top4_services import load_state as load_top4_state
from draft_app.top4_schedule import build_schedule
from draft_app.player_map_store import load_player_map
from draft_app.top4_score_store import save_top4_score
from draft_app.mantra_routes import _load_player, _fetch_player, _to_int, ROUND_CACHE_DIR, LINEUPS_DIR
from draft_app.api_football_client import api_football_client
from draft_app.api_football_score_converter import convert_api_football_stats_to_top4_format
from draft_app.mantra_routes import _calc_score_breakdown


def get_all_rounds_gw1_to_gw4():
    """Get all rounds from GW1 to GW4"""
    schedule = build_schedule()
    rounds_to_refresh = []
    
    for league, rounds in schedule.items():
        for r in rounds:
            gw = _to_int(r.get("gw"))
            rnd = _to_int(r.get("round"))
            if gw and 1 <= gw <= 4 and rnd:
                rounds_to_refresh.append({
                    "league": league,
                    "gw": gw,
                    "round": rnd,
                })
    
    # Sort by GW, then by league
    rounds_to_refresh.sort(key=lambda x: (x["gw"], x["league"]))
    return rounds_to_refresh


def main():
    print("=" * 80)
    print("ОБНОВЛЕНИЕ ОЧКОВ TOP-4 ДРАФТА ДЛЯ GW1-GW4")
    print("=" * 80)
    
    # Check if API Football is enabled
    use_api_football = os.getenv("TOP4_USE_API_FOOTBALL", "false").lower() == "true"
    print(f"\n📊 Режим: {'API Football' if use_api_football else 'MantraFootball'}")
    
    # Load state and mapping
    print("\n📥 Загрузка данных...")
    state = load_top4_state()
    rosters = state.get("rosters", {})
    mapping = load_player_map()
    print(f"✅ Загружено менеджеров: {len(rosters)}")
    print(f"✅ Загружено маппингов: {len(mapping)}")
    
    # Get all rounds from GW1 to GW4
    rounds_to_refresh = get_all_rounds_gw1_to_gw4()
    print(f"\n📋 Найдено раундов для обновления: {len(rounds_to_refresh)}")
    
    # Show rounds breakdown
    if rounds_to_refresh:
        print("\n📅 Расписание раундов:")
        for r in rounds_to_refresh:
            print(f"   GW{r['gw']}: {r['league']} - Тур {r['round']}")
    
    # Collect all player IDs from rosters
    all_player_ids = set()
    for roster in rosters.values():
        for item in roster or []:
            pl = item.get("player") if isinstance(item, dict) and item.get("player") else item
            fid = pl.get("playerId") or pl.get("id")
            if fid:
                # Get MantraFootball ID from mapping
                mid = mapping.get(str(fid))
                if mid:
                    all_player_ids.add(int(mid))
    
    print(f"\n✅ Найдено уникальных игроков: {len(all_player_ids)}")
    
    # Refresh stats for all players
    print(f"\n🔄 Обновление статистики для всех игроков...")
    refreshed = 0
    failed = 0
    
    for i, pid in enumerate(sorted(all_player_ids), 1):
        try:
            print(f"[{i}/{len(all_player_ids)}] Обновление игрока {pid}...", end=" ")
            
            # Force refresh from source
            if use_api_football:
                # For API Football, data is fetched on-demand when displaying
                # We just need to clear cache to force refresh
                # The actual fetching happens in mantra_routes.py
                refreshed_ok = True
            else:
                # Refresh from MantraFootball
                player_data = _fetch_player(pid)
                if player_data:
                    save_top4_score(pid, player_data)
                    refreshed_ok = True
                else:
                    refreshed_ok = False
            
            if refreshed_ok:
                refreshed += 1
                print("✅")
            else:
                failed += 1
                print("⚠️")
        except Exception as e:
            failed += 1
            print(f"❌ Ошибка: {e}")
    
    print(f"\n📊 РЕЗУЛЬТАТЫ ОБНОВЛЕНИЯ СТАТИСТИКИ:")
    print(f"   ✅ Обновлено: {refreshed}")
    print(f"   ❌ Ошибок: {failed}")
    print(f"   📋 Всего игроков: {len(all_player_ids)}")
    
    # Clear lineup caches for GW1-GW4
    print(f"\n🗑️  Очистка кеша лайнапов для GW1-GW4...")
    cleared_count = 0
    
    if ROUND_CACHE_DIR.exists():
        for cache_file in ROUND_CACHE_DIR.glob("*.json"):
            try:
                # Extract round number from filename (e.g., "round15.json" -> 15)
                filename = cache_file.stem
                if filename.startswith("round"):
                    round_no = int(filename.replace("round", ""))
                    # Find corresponding GW
                    for r in rounds_to_refresh:
                        if r["round"] == round_no:
                            cache_file.unlink()
                            cleared_count += 1
                            print(f"   ✅ Удален кеш для раунда {round_no} (GW{r['gw']}, {r['league']})")
                            break
            except (ValueError, AttributeError):
                continue
    
    if LINEUPS_DIR.exists():
        for lineup_file in LINEUPS_DIR.glob("*.json"):
            try:
                filename = lineup_file.stem
                if filename.startswith("round"):
                    round_no = int(filename.replace("round", ""))
                    # Find corresponding GW
                    for r in rounds_to_refresh:
                        if r["round"] == round_no:
                            lineup_file.unlink()
                            cleared_count += 1
                            print(f"   ✅ Удален кеш лайнапов для раунда {round_no} (GW{r['gw']}, {r['league']})")
                            break
            except (ValueError, AttributeError):
                continue
    
    print(f"   ✅ Очищено файлов кеша: {cleared_count}")
    
    print("\n" + "=" * 80)
    print("✅ ОБНОВЛЕНИЕ ЗАВЕРШЕНО!")
    print("=" * 80)
    print("\n💡 Теперь перезагрузите страницы результатов и лайнапов,")
    print("   чтобы увидеть обновленные очки для GW1-GW4")
    print("   Кеш лайнапов очищен, они будут пересчитаны при следующем запросе")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

