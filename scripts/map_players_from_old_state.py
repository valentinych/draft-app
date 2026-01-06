#!/usr/bin/env python3
"""
Map players from old draft_state_top4.json file to API Football IDs.

This script:
1. Loads the old draft_state_top4.json file
2. Extracts all players from rosters
3. Attempts to map them using the improved mapping algorithm
4. Saves the mapping results
"""
import sys
import os
import json
from pathlib import Path
from typing import Dict, List, Set, Optional, Any

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.api_football_client import api_football_client, LEAGUE_IDS
from draft_app.top4_services import load_players as load_top4_players
from draft_app.player_map_store import load_top4_player_map, save_top4_player_map
from draft_app.mantra_api import PlayerMatcher


def load_old_state(file_path: str) -> Dict[str, Any]:
    """Load the old draft_state_top4.json file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_players_from_old_state(state: Dict[str, Any]) -> List[Dict]:
    """Extract all unique players from rosters in the old state"""
    players = []
    seen_ids = set()
    
    rosters = state.get("rosters", {})
    for manager, roster in rosters.items():
        if not isinstance(roster, list):
            continue
        
        for player in roster:
            if not isinstance(player, dict):
                continue
            
            player_id = str(player.get("playerId") or player.get("id", ""))
            if not player_id or player_id in seen_ids:
                continue
            
            seen_ids.add(player_id)
            players.append(player)
    
    return players


def normalize_old_player(player: Dict) -> Dict:
    """Normalize player from old state for matching"""
    return {
        "draft_id": str(player.get("playerId") or player.get("id", "")),
        "name": player.get("fullName") or player.get("name", ""),
        "club": player.get("clubName") or player.get("club", ""),
        "position": player.get("position", ""),
        "league": player.get("league", ""),
    }


def normalize_api_football_player(api_player: Dict) -> Dict:
    """Normalize API Football player data for matching"""
    if "player" in api_player:
        player_info = api_player.get("player", {})
        team_info = api_player.get("team", {})
    else:
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


def perform_mapping_for_old_players(
    old_players: List[Dict],
    api_players: Dict[str, List[Dict]],
    existing_mapping: Dict[str, str]
) -> Dict[str, str]:
    """Perform mapping between old draft players and API Football players"""
    print("\n" + "=" * 80)
    print("ВЫПОЛНЕНИЕ МАППИНГА ДЛЯ ИГРОКОВ ИЗ СТАРОГО ФАЙЛА")
    print("=" * 80)
    
    matcher = PlayerMatcher()
    new_mapping = existing_mapping.copy()
    
    # Normalize old players
    print("\n📋 Нормализация игроков из старого файла...")
    normalized_old = {}
    for player in old_players:
        norm = normalize_old_player(player)
        draft_id = norm["draft_id"]
        if draft_id:
            normalized_old[draft_id] = norm
    
    print(f"   ✅ Нормализовано игроков: {len(normalized_old)}")
    
    # Process each league
    total_mapped = 0
    total_new = 0
    total_updated = 0
    unmapped_players = []
    
    for league_name, players in api_players.items():
        print(f"\n🔄 Обработка лиги: {league_name}")
        league_mapped = 0
        league_new = 0
        league_updated = 0
        
        for old_draft_id, norm_old in normalized_old.items():
            # Skip if already mapped
            if old_draft_id in new_mapping.values():
                continue
            
            # Only match players from the same league if league is specified
            old_league = norm_old.get("league", "")
            if old_league and old_league != league_name:
                continue
            
            # Find best match in API Football players
            best_match = None
            best_score = 0.0
            best_name_score = 0.0
            best_club_score = 0.0
            
            for api_player in players:
                try:
                    norm_api = normalize_api_football_player(api_player)
                    api_id = norm_api.get("api_football_id")
                    
                    if not api_id:
                        continue
                    
                    # Skip if this api_id is already mapped to a different draft_id
                    api_id_str = str(api_id)
                    if api_id_str in new_mapping and new_mapping[api_id_str] != old_draft_id:
                        continue
                    
                    # Use PlayerMatcher's advanced similarity methods
                    name_score = matcher.calculate_name_similarity(norm_api["name"], norm_old["name"])
                    club_score = matcher.calculate_club_similarity(norm_api["club"], norm_old["club"])
                    
                    # Combined score (weighted: name is more important, but club must match reasonably)
                    if club_score >= 0.7:
                        combined_score = (name_score * 0.6) + (club_score * 0.4)
                        threshold = 0.5
                    elif club_score >= 0.4:
                        combined_score = (name_score * 0.7) + (club_score * 0.3)
                        threshold = 0.6
                    else:
                        continue
                    
                    if combined_score > best_score and combined_score >= threshold:
                        best_score = combined_score
                        best_name_score = name_score
                        best_club_score = club_score
                        best_match = api_id_str
                        
                except Exception as e:
                    continue
            
            if best_match:
                existing_draft_id = new_mapping.get(best_match)
                
                if existing_draft_id != old_draft_id:
                    new_mapping[best_match] = old_draft_id
                    league_mapped += 1
                    
                    if existing_draft_id:
                        league_updated += 1
                    else:
                        league_new += 1
                    
                    # Debug output
                    print(f"      ✅ {norm_old['name']} ({norm_old['club']}) [ID:{old_draft_id}] -> API ID:{best_match} [name:{best_name_score:.2f} club:{best_club_score:.2f} total:{best_score:.2f}]")
            else:
                # Track unmapped players
                unmapped_players.append({
                    "draft_id": old_draft_id,
                    "name": norm_old["name"],
                    "club": norm_old["club"],
                    "league": norm_old.get("league", "Mixed"),
                    "position": norm_old.get("position", "Unknown")
                })
        
        print(f"   ✅ Замаплено: {league_mapped} (новых: {league_new}, обновлено: {league_updated})")
        total_mapped += league_mapped
        total_new += league_new
        total_updated += league_updated
    
    print(f"\n✅ Всего замаплено: {total_mapped} (новых: {total_new}, обновлено: {total_updated})")
    
    if unmapped_players:
        print(f"\n⚠️  Не замаплено игроков: {len(unmapped_players)}")
        print("\n📋 Список незамапленных игроков:")
        for player in unmapped_players[:20]:  # Show first 20
            print(f"   • {player['name']} ({player['club']}) - {player['league']} - ID: {player['draft_id']}")
        if len(unmapped_players) > 20:
            print(f"   ... и еще {len(unmapped_players) - 20} игроков")
    
    return new_mapping


def main():
    print("=" * 80)
    print("МАППИНГ ИГРОКОВ ИЗ СТАРОГО DRAFT_STATE_TOP4.JSON")
    print("=" * 80)
    
    # Path to old file - try multiple locations
    old_file = None
    possible_paths = [
        BASE_DIR / "draft_state_top4 (6).json",
        Path("/Users/ruslan.aharodnik/Downloads/draft_state_top4 (6).json"),
        Path.home() / "Downloads" / "draft_state_top4 (6).json",
    ]
    
    for path in possible_paths:
        if path.exists():
            old_file = path
            break
    
    if not old_file or not old_file.exists():
        print(f"\n❌ Файл не найден в следующих местах:")
        for path in possible_paths:
            print(f"   • {path}")
        print("\n   Убедитесь, что файл draft_state_top4 (6).json находится в одном из этих мест")
        return 1
    
    # Load old state
    print(f"\n📥 Загрузка старого файла: {old_file}")
    try:
        old_state = load_old_state(str(old_file))
    except Exception as e:
        print(f"❌ Ошибка при загрузке файла: {e}")
        return 1
    
    # Extract players
    print("\n📋 Извлечение игроков из ростеров...")
    old_players = extract_players_from_old_state(old_state)
    print(f"   ✅ Найдено уникальных игроков: {len(old_players)}")
    
    # Load current Top-4 players to get real data
    print("\n📥 Загрузка текущих игроков из Top-4 системы...")
    current_top4_players = load_top4_players()
    print(f"   ✅ Загружено игроков из Top-4 системы: {len(current_top4_players)}")
    
    # Create index by playerId
    top4_players_by_id = {}
    for player in current_top4_players:
        player_id = str(player.get("playerId") or player.get("id", ""))
        if player_id:
            top4_players_by_id[player_id] = player
    
    # Enrich old players with real data from Top-4 system
    print("\n🔄 Обогащение данных игроков из старого файла...")
    enriched_players = []
    for old_player in old_players:
        old_id = str(old_player.get("playerId") or old_player.get("id", ""))
        if old_id in top4_players_by_id:
            # Use real data from Top-4 system
            real_player = top4_players_by_id[old_id]
            enriched_player = {
                "playerId": old_id,
                "fullName": real_player.get("fullName") or old_player.get("fullName", ""),
                "clubName": real_player.get("clubName") or old_player.get("clubName", ""),
                "position": real_player.get("position") or old_player.get("position", ""),
                "league": real_player.get("league") or old_player.get("league", ""),
            }
            enriched_players.append(enriched_player)
            print(f"      ✅ Найден: {enriched_player['fullName']} ({enriched_player['clubName']}) - ID: {old_id}")
        else:
            # Keep old data if not found
            enriched_players.append(old_player)
            print(f"      ⚠️  Не найден в Top-4 системе: ID {old_id}")
    
    old_players = enriched_players
    print(f"\n   ✅ Обогащено игроков: {len(enriched_players)}")
    
    # Load existing mapping
    print("\n📥 Загрузка существующего Top-4 маппинга...")
    existing_mapping = load_top4_player_map()
    print(f"   ✅ Загружено существующих маппингов: {len(existing_mapping)}")
    
    # Load all API Football players
    print("\n" + "=" * 80)
    print("ЗАГРУЗКА ВСЕХ ИГРОКОВ ИЗ API FOOTBALL")
    print("=" * 80)
    
    api_players = {}
    for league_name, league_id in LEAGUE_IDS.items():
        print(f"\n📥 Загрузка игроков из {league_name} (league_id={league_id})...")
        try:
            players = api_football_client.get_players(league_id, 2025)
            if players:
                api_players[league_name] = players
                print(f"   ✅ Загружено игроков: {len(players)}")
        except Exception as e:
            print(f"   ⚠️  Ошибка при загрузке: {e}")
            continue
    
    total_api_players = sum(len(players) for players in api_players.values())
    print(f"\n✅ Всего загружено игроков из API Football: {total_api_players}")
    
    if not api_players:
        print("\n❌ ОШИБКА: Не удалось загрузить игроков из API Football")
        return 1
    
    # Perform mapping
    new_mapping = perform_mapping_for_old_players(old_players, api_players, existing_mapping)
    
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
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

