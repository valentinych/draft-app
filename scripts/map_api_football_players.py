#!/usr/bin/env python3
"""
Map API Football players to existing Top-4 draft players
Matches players by club name and surname, continues until all players are found

Usage:
    python3 scripts/map_api_football_players.py
    or
    heroku run --app val-draft-app "python3 scripts/map_api_football_players.py"
"""
import sys
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from difflib import SequenceMatcher

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.top4_services import (
    load_players as load_top4_players,
    players_index as top4_players_index,
    load_state as load_top4_state,
)
from draft_app.api_football_client import api_football_client
from draft_app.player_map_store import load_player_map, save_player_map


def normalize_name(name: str) -> str:
    """Normalize player name for comparison"""
    if not name:
        return ""
    # Remove extra spaces, convert to lowercase
    name = " ".join(name.split()).lower()
    # Remove common prefixes/suffixes
    name = name.replace("jr.", "").replace("sr.", "").replace("ii", "").replace("iii", "")
    return name.strip()


def extract_surname(full_name: str) -> str:
    """Extract surname from full name"""
    if not full_name:
        return ""
    parts = full_name.split()
    if len(parts) > 0:
        return parts[-1].lower()
    return ""


def normalize_club(club: str) -> str:
    """Normalize club name for comparison"""
    if not club:
        return ""
    club = club.lower().strip()
    # Remove common suffixes
    club = club.replace(" fc", "").replace(" cf", "").replace(" cf.", "").replace(" f.c.", "")
    club = club.replace(" united", " utd").replace(" city", "").replace(" town", "")
    return club.strip()


def similarity_score(str1: str, str2: str) -> float:
    """Calculate similarity score between two strings (0.0 to 1.0)"""
    if not str1 or not str2:
        return 0.0
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


def find_best_match(
    api_player: Dict,
    draft_players: List[Dict],
    existing_mapping: Dict[str, str]
) -> Optional[Tuple[Dict, float]]:
    """
    Find best matching draft player for API Football player
    
    Returns:
        Tuple of (draft_player, similarity_score) or None
    """
    api_name = api_player.get("name", "")
    api_surname = extract_surname(api_name)
    api_club = normalize_club(api_player.get("team", {}).get("name", ""))
    api_id = str(api_player.get("api_football_id", ""))
    
    if not api_name or not api_club:
        return None
    
    # Skip if already mapped
    if api_id in existing_mapping:
        return None
    
    best_match = None
    best_score = 0.0
    
    for draft_player in draft_players:
        draft_id = str(draft_player.get("playerId", ""))
        
        # Skip if already mapped to another API player
        if any(mapped_api_id == api_id for mapped_api_id, mapped_draft_id in existing_mapping.items() if mapped_draft_id == draft_id):
            continue
        
        draft_name = draft_player.get("fullName", "")
        draft_surname = extract_surname(draft_name)
        draft_club = normalize_club(draft_player.get("clubName", ""))
        
        if not draft_name or not draft_club:
            continue
        
        # Calculate similarity scores
        name_similarity = similarity_score(api_name, draft_name)
        surname_similarity = similarity_score(api_surname, draft_surname) if api_surname and draft_surname else 0.0
        club_similarity = similarity_score(api_club, draft_club)
        
        # Combined score (club is most important, then surname, then full name)
        combined_score = (
            club_similarity * 0.5 +  # Club match is critical
            surname_similarity * 0.4 +  # Surname is very important
            name_similarity * 0.1  # Full name helps
        )
        
        # Require minimum thresholds
        if club_similarity < 0.7:  # Club must match reasonably well
            continue
        if surname_similarity < 0.6 and name_similarity < 0.7:  # Name must match reasonably well
            continue
        
        if combined_score > best_score:
            best_score = combined_score
            best_match = draft_player
    
    if best_match and best_score >= 0.7:  # Minimum threshold for acceptance
        return (best_match, best_score)
    
    return None


def map_players_iteratively(
    api_players: List[Dict],
    draft_players: List[Dict],
    existing_mapping: Dict[str, str]
) -> Dict[str, str]:
    """
    Map API Football players to draft players iteratively
    Continues until all possible matches are found
    """
    mapping = dict(existing_mapping)
    unmatched_api = []
    
    print(f"\n📊 Начало маппинга:")
    print(f"   API Football игроков: {len(api_players)}")
    print(f"   Draft игроков: {len(draft_players)}")
    print(f"   Уже замапплено: {len(mapping)}")
    
    # First pass: strict matching
    print(f"\n🔍 Первый проход (строгое сопоставление)...")
    for api_player in api_players:
        match = find_best_match(api_player, draft_players, mapping)
        if match:
            draft_player, score = match
            api_id = str(api_player.get("api_football_id", ""))
            draft_id = str(draft_player.get("playerId", ""))
            mapping[api_id] = draft_id
            print(f"   ✅ {api_player.get('name')} ({api_player.get('team', {}).get('name')}) -> {draft_player.get('fullName')} ({draft_player.get('clubName')}) [score: {score:.2f}]")
        else:
            unmatched_api.append(api_player)
    
    # Second pass: more lenient matching for unmatched players
    if unmatched_api:
        print(f"\n🔍 Второй проход (более мягкое сопоставление для {len(unmatched_api)} игроков)...")
        for api_player in unmatched_api[:]:  # Copy list to iterate safely
            # Try with lower thresholds
            api_name = api_player.get("name", "")
            api_surname = extract_surname(api_name)
            api_club = normalize_club(api_player.get("team", {}).get("name", ""))
            api_id = str(api_player.get("api_football_id", ""))
            
            if api_id in mapping:
                unmatched_api.remove(api_player)
                continue
            
            best_match = None
            best_score = 0.0
            
            for draft_player in draft_players:
                draft_id = str(draft_player.get("playerId", ""))
                
                # Skip if already mapped
                if draft_id in mapping.values():
                    continue
                
                draft_name = draft_player.get("fullName", "")
                draft_surname = extract_surname(draft_name)
                draft_club = normalize_club(draft_player.get("clubName", ""))
                
                club_similarity = similarity_score(api_club, draft_club)
                surname_similarity = similarity_score(api_surname, draft_surname) if api_surname and draft_surname else 0.0
                name_similarity = similarity_score(api_name, draft_name)
                
                # More lenient thresholds
                if club_similarity < 0.5:
                    continue
                if surname_similarity < 0.4 and name_similarity < 0.5:
                    continue
                
                combined_score = club_similarity * 0.5 + surname_similarity * 0.4 + name_similarity * 0.1
                
                if combined_score > best_score:
                    best_score = combined_score
                    best_match = draft_player
            
            if best_match and best_score >= 0.6:  # Lower threshold for second pass
                draft_id = str(best_match.get("playerId", ""))
                mapping[api_id] = draft_id
                unmatched_api.remove(api_player)
                print(f"   ✅ {api_player.get('name')} ({api_player.get('team', {}).get('name')}) -> {best_match.get('fullName')} ({best_match.get('clubName')}) [score: {best_score:.2f}]")
    
    # Third pass: show unmatched players for manual review
    if unmatched_api:
        print(f"\n⚠️  Не найдено совпадений для {len(unmatched_api)} игроков:")
        for api_player in unmatched_api[:20]:  # Show first 20
            print(f"   • {api_player.get('name')} ({api_player.get('team', {}).get('name')}) - API ID: {api_player.get('api_football_id')}")
        if len(unmatched_api) > 20:
            print(f"   ... и еще {len(unmatched_api) - 20} игроков")
    
    return mapping


def main():
    print("=" * 80)
    print("МАППИНГ ИГРОКОВ API FOOTBALL К DRAFT ИГРОКАМ")
    print("=" * 80)
    
    # Load existing mapping
    existing_mapping = load_player_map()
    print(f"\n📋 Загружен существующий маппинг: {len(existing_mapping)} записей")
    
    # Load draft players
    print("\n📥 Загрузка draft игроков...")
    draft_players = load_top4_players()
    print(f"✅ Загружено draft игроков: {len(draft_players)}")
    
    # Load draft state to get drafted players
    print("\n📥 Загрузка состояния драфта...")
    state = load_top4_state()
    rosters = state.get("rosters", {})
    
    # Get all drafted player IDs
    drafted_ids = set()
    for roster in rosters.values():
        for item in roster or []:
            pl = item.get("player") if isinstance(item, dict) and item.get("player") else item
            pid = str(pl.get("playerId") or pl.get("id", ""))
            if pid:
                drafted_ids.add(pid)
    
    print(f"✅ Найдено задрафтованных игроков: {len(drafted_ids)}")
    
    # Filter draft players to only those that are drafted
    drafted_players = [p for p in draft_players if str(p.get("playerId", "")) in drafted_ids]
    print(f"✅ Draft игроков для маппинга: {len(drafted_players)}")
    
    # Load API Football players
    print("\n📥 Загрузка игроков из API Football...")
    try:
        api_players_data = api_football_client.get_all_top4_players(season=2024)
        print(f"✅ Загружено игроков из API Football: {len(api_players_data)}")
    except Exception as e:
        print(f"❌ Ошибка загрузки из API Football: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Perform mapping
    print("\n" + "=" * 80)
    new_mapping = map_players_iteratively(api_players_data, drafted_players, existing_mapping)
    
    # Merge with existing mapping
    final_mapping = {**existing_mapping, **new_mapping}
    
    print("\n" + "=" * 80)
    print("📊 РЕЗУЛЬТАТЫ МАППИНГА:")
    print(f"   Всего замапплено: {len(final_mapping)}")
    print(f"   Новых маппингов: {len(new_mapping)}")
    print(f"   Уже было: {len(existing_mapping)}")
    
    # Show some examples
    if new_mapping:
        print(f"\n📋 Примеры новых маппингов (первые 10):")
        for i, (api_id, draft_id) in enumerate(list(new_mapping.items())[:10]):
            api_player = next((p for p in api_players_data if str(p.get("api_football_id", "")) == api_id), None)
            draft_player = next((p for p in drafted_players if str(p.get("playerId", "")) == draft_id), None)
            if api_player and draft_player:
                print(f"   {i+1}. API ID {api_id} ({api_player.get('name')}) -> Draft ID {draft_id} ({draft_player.get('fullName')})")
    
    # Save mapping
    print(f"\n💾 Сохранение маппинга...")
    # Convert to format expected by player_map_store (str -> int)
    int_mapping = {}
    for api_id, draft_id in final_mapping.items():
        try:
            int_mapping[str(api_id)] = int(draft_id)
        except (ValueError, TypeError):
            print(f"⚠️  Пропущен маппинг с невалидным ID: {api_id} -> {draft_id}")
            continue
    
    save_player_map(int_mapping)
    print(f"✅ Маппинг сохранен! ({len(int_mapping)} записей)")
    
    print("\n" + "=" * 80)
    print("✅ МАППИНГ ЗАВЕРШЕН!")
    print("=" * 80)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

