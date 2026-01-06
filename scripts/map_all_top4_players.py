#!/usr/bin/env python3
"""
Map ALL players from Top-4 leagues (EPL, La Liga, Serie A, Bundesliga)
between API Football and Top-4 draft system.

This script:
1. Loads ALL players from API Football for all 4 leagues
2. Loads ALL players from Top-4 draft system
3. Performs mapping for ALL players (not just drafted ones)
4. Validates mapping results
5. Saves updated mapping

Usage:
    python3 scripts/map_all_top4_players.py
    or
    heroku run --app val-draft-app "python3 scripts/map_all_top4_players.py"
"""
import sys
import os
from pathlib import Path
from typing import Dict, List, Set, Optional, Any
from collections import defaultdict
from difflib import SequenceMatcher

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.api_football_client import api_football_client, LEAGUE_IDS
from draft_app.top4_services import load_players as load_top4_players
from draft_app.player_map_store import load_player_map, save_player_map, load_top4_player_map, save_top4_player_map
from draft_app.mantra_api import PlayerMatcher


def load_all_api_football_players() -> Dict[str, List[Dict]]:
    """Load ALL players from API Football for all 4 leagues"""
    print("=" * 80)
    print("ЗАГРУЗКА ВСЕХ ИГРОКОВ ИЗ API FOOTBALL")
    print("=" * 80)
    
    all_players = {}
    
    for league_name, league_id in LEAGUE_IDS.items():
        print(f"\n📥 Загрузка игроков из {league_name} (league_id={league_id})...")
        try:
            players = api_football_client.get_players(league_id, 2025)
            if players:
                # Add league name to each player for filtering
                for player in players:
                    player["_league"] = league_name
                all_players[league_name] = players
                print(f"   ✅ Загружено игроков: {len(players)}")
            else:
                print(f"   ⚠️  Не удалось загрузить игроков")
                all_players[league_name] = []
        except Exception as e:
            print(f"   ❌ Ошибка при загрузке: {e}")
            import traceback
            traceback.print_exc()
            all_players[league_name] = []
    
    total_players = sum(len(players) for players in all_players.values())
    print(f"\n✅ Всего загружено игроков из API Football: {total_players}")
    
    return all_players


def load_all_top4_draft_players() -> List[Dict]:
    """Load ALL players from Top-4 draft system"""
    print("\n" + "=" * 80)
    print("ЗАГРУЗКА ВСЕХ ИГРОКОВ ИЗ TOP-4 DRAFT")
    print("=" * 80)
    
    try:
        players = load_top4_players()
        print(f"✅ Загружено игроков из Top-4 draft: {len(players)}")
        return players
    except Exception as e:
        print(f"❌ Ошибка при загрузке игроков из Top-4 draft: {e}")
        return []


def normalize_api_football_player(api_player: Dict) -> Dict:
    """Normalize API Football player data for matching"""
    # Handle different API Football response formats
    if "player" in api_player:
        # Format from get_players endpoint
        player_info = api_player.get("player", {})
        team_info = api_player.get("team", {})
    else:
        # Format from get_all_top4_players
        player_info = api_player
        team_info = api_player.get("team", {})
    
    return {
        "api_football_id": player_info.get("id") or api_player.get("api_football_id"),
        "name": player_info.get("name", "") or api_player.get("name", ""),
        "firstname": player_info.get("firstname", ""),
        "lastname": player_info.get("lastname", ""),
        "club": team_info.get("name", "") if isinstance(team_info, dict) else (api_player.get("club", "") if isinstance(api_player.get("club"), str) else ""),
        "club_id": team_info.get("id") if isinstance(team_info, dict) else None,
        "position": api_football_client._normalize_position(player_info.get("position", "") or api_player.get("position", "")),
    }


def normalize_top4_player(draft_player: Dict) -> Dict:
    """Normalize Top-4 draft player data for matching"""
    return {
        "draft_id": str(draft_player.get("playerId") or draft_player.get("id", "")),
        "name": draft_player.get("fullName") or draft_player.get("name", ""),
        "club": draft_player.get("clubName") or draft_player.get("club", ""),
        "position": draft_player.get("position", ""),
        "league": draft_player.get("league", ""),
    }


def perform_mapping(
    api_players: Dict[str, List[Dict]],
    draft_players: List[Dict],
    existing_mapping: Dict[str, str]
) -> Dict[str, str]:
    """Perform mapping between API Football and Top-4 draft players"""
    print("\n" + "=" * 80)
    print("ВЫПОЛНЕНИЕ МАППИНГА")
    print("=" * 80)
    
    matcher = PlayerMatcher()
    new_mapping = existing_mapping.copy()
    
    # Normalize draft players
    print("\n📋 Нормализация игроков из Top-4 draft...")
    normalized_draft = {}
    for player in draft_players:
        norm = normalize_top4_player(player)
        draft_id = norm["draft_id"]
        if draft_id:
            normalized_draft[draft_id] = norm
    
    print(f"   ✅ Нормализовано игроков из Top-4 draft: {len(normalized_draft)}")
    
    # Process each league
    total_mapped = 0
    total_new = 0
    total_updated = 0
    
    for league_name, players in api_players.items():
        print(f"\n🔄 Обработка лиги: {league_name}")
        league_mapped = 0
        league_new = 0
        league_updated = 0
        
        for api_player in players:
            try:
                norm_api = normalize_api_football_player(api_player)
                api_id = norm_api.get("api_football_id")
                
                if not api_id:
                    continue
                
                # Find best match in draft players
                best_match = None
                best_score = 0.0
                
                for draft_id, norm_draft in normalized_draft.items():
                    # Only match players from the same league
                    draft_league = norm_draft.get("league", "")
                    if draft_league and draft_league != league_name:
                        continue
                    
                    # Calculate similarity using PlayerMatcher methods
                    norm_api_name = matcher.normalize_name(norm_api["name"])
                    norm_draft_name = matcher.normalize_name(norm_draft["name"])
                    norm_api_club = matcher.normalize_club_name(norm_api["club"])
                    norm_draft_club = matcher.normalize_club_name(norm_draft["club"])
                    
                    # Simple similarity calculation
                    name_score = SequenceMatcher(None, norm_api_name, norm_draft_name).ratio()
                    club_score = SequenceMatcher(None, norm_api_club, norm_draft_club).ratio()
                    
                    # Combined score (weighted)
                    combined_score = (name_score * 0.7) + (club_score * 0.3)
                    
                    if combined_score > best_score and combined_score >= 0.7:  # Minimum threshold
                        best_score = combined_score
                        best_match = draft_id
                
                if best_match:
                    api_id_str = str(api_id)
                    existing_draft_id = new_mapping.get(api_id_str)
                    
                    if existing_draft_id != best_match:
                        new_mapping[api_id_str] = best_match
                        league_mapped += 1
                        
                        if existing_draft_id:
                            league_updated += 1
                        else:
                            league_new += 1
                            
            except Exception as e:
                print(f"   ⚠️  Ошибка при обработке игрока: {e}")
                continue
        
        print(f"   ✅ Замаплено: {league_mapped} (новых: {league_new}, обновлено: {league_updated})")
        total_mapped += league_mapped
        total_new += league_new
        total_updated += league_updated
    
    print(f"\n✅ Всего замаплено: {total_mapped} (новых: {total_new}, обновлено: {total_updated})")
    
    return new_mapping


def validate_mapping(
    mapping: Dict[str, str],
    api_players: Dict[str, List[Dict]],
    draft_players: List[Dict]
) -> Dict[str, Any]:
    """Validate mapping results"""
    print("\n" + "=" * 80)
    print("ПРОВЕРКА РЕЗУЛЬТАТОВ МАППИНГА")
    print("=" * 80)
    
    # Normalize draft players
    normalized_draft = {}
    for player in draft_players:
        norm = normalize_top4_player(player)
        draft_id = norm["draft_id"]
        if draft_id:
            normalized_draft[draft_id] = norm
    
    # Count API Football players
    total_api_players = sum(len(players) for players in api_players.values())
    
    # Count mapped players
    mapped_count = len(mapping)
    
    # Count draft players
    total_draft_players = len(normalized_draft)
    
    # Count draft players that are mapped
    mapped_draft_ids = set(mapping.values())
    mapped_draft_count = len(mapped_draft_ids)
    
    # Count API Football players that are mapped
    mapped_api_ids = set(mapping.keys())
    mapped_api_count = len(mapped_api_ids)
    
    # Count API Football players with valid IDs
    valid_api_ids = set()
    for players in api_players.values():
        for player in players:
            api_id = normalize_api_football_player(player).get("api_football_id")
            if api_id:
                valid_api_ids.add(str(api_id))
    
    valid_api_count = len(valid_api_ids)
    
    # Calculate coverage
    api_coverage = (mapped_api_count / valid_api_count * 100) if valid_api_count > 0 else 0
    draft_coverage = (mapped_draft_count / total_draft_players * 100) if total_draft_players > 0 else 0
    
    # Check for duplicates (multiple API IDs mapping to same draft ID)
    draft_id_to_api_ids = defaultdict(list)
    for api_id, draft_id in mapping.items():
        draft_id_to_api_ids[draft_id].append(api_id)
    
    duplicates = {draft_id: api_ids for draft_id, api_ids in draft_id_to_api_ids.items() if len(api_ids) > 1}
    
    results = {
        "total_api_players": total_api_players,
        "valid_api_ids": valid_api_count,
        "mapped_api_count": mapped_api_count,
        "api_coverage": api_coverage,
        "total_draft_players": total_draft_players,
        "mapped_draft_count": mapped_draft_count,
        "draft_coverage": draft_coverage,
        "total_mappings": mapped_count,
        "duplicates": len(duplicates),
        "duplicate_details": duplicates,
    }
    
    print(f"\n📊 СТАТИСТИКА МАППИНГА:")
    print(f"   • Всего игроков в API Football: {total_api_players}")
    print(f"   • Валидных ID в API Football: {valid_api_count}")
    print(f"   • Замаплено из API Football: {mapped_api_count} ({api_coverage:.1f}%)")
    print(f"   • Всего игроков в Top-4 draft: {total_draft_players}")
    print(f"   • Замаплено из Top-4 draft: {mapped_draft_count} ({draft_coverage:.1f}%)")
    print(f"   • Всего маппингов: {mapped_count}")
    print(f"   • Дубликатов (несколько API ID → один draft ID): {len(duplicates)}")
    
    if duplicates:
        print(f"\n⚠️  ДУБЛИКАТЫ (первые 10):")
        for i, (draft_id, api_ids) in enumerate(list(duplicates.items())[:10], 1):
            print(f"   {i}. Draft ID {draft_id} ← API IDs: {', '.join(api_ids[:3])}{'...' if len(api_ids) > 3 else ''}")
    
    return results


def main():
    print("=" * 80)
    print("МАППИНГ ВСЕХ ИГРОКОВ ИЗ TOP-4 ЛИГ")
    print("=" * 80)
    
    # Load existing Top-4 mapping
    print("\n📥 Загрузка существующего Top-4 маппинга...")
    existing_mapping = load_top4_player_map()
    print(f"   ✅ Загружено существующих маппингов: {len(existing_mapping)}")
    
    # Load all API Football players
    api_players = load_all_api_football_players()
    
    # Load all Top-4 draft players
    draft_players = load_all_top4_draft_players()
    
    if not api_players or not draft_players:
        print("\n❌ ОШИБКА: Не удалось загрузить игроков")
        return 1
    
    # Perform mapping
    new_mapping = perform_mapping(api_players, draft_players, existing_mapping)
    
    # Validate mapping
    validation_results = validate_mapping(new_mapping, api_players, draft_players)
    
    # Save mapping
    print("\n" + "=" * 80)
    print("СОХРАНЕНИЕ МАППИНГА")
    print("=" * 80)
    
    try:
        save_top4_player_map(new_mapping)
        print(f"✅ Маппинг сохранен: {len(new_mapping)} записей")
    except Exception as e:
        print(f"❌ Ошибка при сохранении маппинга: {e}")
        return 1
    
    print("\n" + "=" * 80)
    print("✅ МАППИНГ ЗАВЕРШЕН УСПЕШНО")
    print("=" * 80)
    print(f"\n📊 ИТОГОВАЯ СТАТИСТИКА:")
    print(f"   • Всего маппингов: {validation_results['total_mappings']}")
    print(f"   • Покрытие API Football: {validation_results['api_coverage']:.1f}%")
    print(f"   • Покрытие Top-4 draft: {validation_results['draft_coverage']:.1f}%")
    print(f"   • Дубликатов: {validation_results['duplicates']}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

