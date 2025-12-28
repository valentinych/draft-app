#!/usr/bin/env python3
"""
Предварительный расчет оптимальных сборных для UCL драфта.
Сохраняет Team of The MD и Непикнутые гении для MD 1-6 в draft_state_ucl.json
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Optional

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.ucl import (
    _json_load,
    _json_dump_atomic,
    _players_from_ucl,
    _player_matchdays,
    _ucl_state_load,
    _ucl_state_save,
    UCL_STATE,
    UCL_PLAYERS,
    UCL_PARTICIPANTS,
    UCL_TOTAL_MATCHDAYS,
    _ucl_points_for_md,
)
from draft_app.ucl_stats_store import get_player_stats_cached, get_player_stats

def _safe_int(value: Any) -> int:
    """Safely convert value to int"""
    if value is None:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0

def _normalize_position(pos_raw: Any) -> str | None:
    """Normalize position to GK, DEF, MID, FWD"""
    if not pos_raw:
        return None
    pos_upper = str(pos_raw).upper()
    if pos_upper.startswith('GOAL') or pos_upper in ('GK', 'GKP'):
        return 'GK'
    elif pos_upper.startswith('DEF'):
        return 'DEF'
    elif pos_upper.startswith('MID'):
        return 'MID'
    elif pos_upper.startswith('FWD') or pos_upper.startswith('FOR'):
        return 'FWD'
    return None

def _resolve_team_id_from_stats(stats: Dict[str, Any]) -> str | None:
    """Extract team ID from stats"""
    if not isinstance(stats, dict):
        return None
    
    data = stats.get("data", {})
    value = data.get("value", {}) if isinstance(data, dict) else {}
    
    # Try various fields
    for candidate in (
        value.get("tId"),
        value.get("teamId"),
        data.get("tId"),
        data.get("teamId"),
        stats.get("tId"),
        stats.get("teamId"),
    ):
        if candidate:
            try:
                return str(int(candidate))
            except Exception:
                pass
    
    return None

def _resolve_club_name_from_stats(stats: Dict[str, Any], player: Dict[str, Any]) -> str:
    """Extract club name from stats or player"""
    if not isinstance(stats, dict):
        return player.get("clubName") or ""
    
    data = stats.get("data", {})
    value = data.get("value", {}) if isinstance(data, dict) else {}
    
    # Try various fields
    for candidate in (
        value.get("tName"),
        value.get("teamName"),
        data.get("tName"),
        data.get("teamName"),
        stats.get("tName"),
        stats.get("teamName"),
        player.get("clubName"),
    ):
        if candidate:
            return str(candidate)
    
    return ""

def _get_player_team_id_from_raw(pid: int, raw_players_data: Any) -> str | None:
    """Extract teamId from raw players data"""
    players_list = []
    if isinstance(raw_players_data, dict):
        players_list = (
            raw_players_data.get("data", {})
            .get("value", {})
            .get("playerList", [])
            if isinstance(raw_players_data.get("data"), dict) else []
        )
    elif isinstance(raw_players_data, list):
        players_list = raw_players_data
    
    for raw_p in players_list:
        if isinstance(raw_p, dict):
            raw_pid = raw_p.get("id") or raw_p.get("playerId")
            if raw_pid and int(raw_pid) == pid:
                raw_team_id = raw_p.get("tId") or raw_p.get("teamId")
                if raw_team_id:
                    try:
                        return str(int(raw_team_id))
                    except Exception:
                        pass
    return None

def build_optimal_team_for_md(
    all_players: List[Dict[str, Any]],
    md: int,
    managers: List[str],
    rosters: Dict[str, List[Dict[str, Any]]],
    exclude_picked: bool = False,
    raw_players_data: Any = None
) -> Dict[str, Any]:
    """Build optimal team for specific MD"""
    # Position limits: GK: 3, DEF: 8, MID: 9, FWD: 5
    pos_limits = {"GK": 3, "DEF": 8, "MID": 9, "FWD": 5}
    
    # Get picked player IDs for this MD
    picked_ids_for_md: Set[int] = set()
    if exclude_picked:
        for manager in managers:
            manager_roster = rosters.get(manager, [])
            for item in manager_roster:
                payload = item.get("player") if isinstance(item, dict) and item.get("player") else item
                if isinstance(payload, dict):
                    matchdays = _player_matchdays(payload)
                    if md in matchdays:
                        pid = payload.get("playerId")
                        if pid:
                            try:
                                picked_ids_for_md.add(int(pid))
                            except Exception:
                                pass
    
    # Filter and process players
    available_players = []
    for player in all_players:
        pid = player.get("playerId")
        if not pid:
            continue
        try:
            pid_int = int(pid)
        except Exception:
            continue
        
        # Skip if exclude_picked and player is picked
        if exclude_picked and pid_int in picked_ids_for_md:
            continue
        
        # Check if player played in this MD
        matchdays = _player_matchdays(player)
        if md not in matchdays:
            continue
        
        # Get teamId from raw players data first (from players_80_en_1.json)
        # In UCL data, teamId is stored as "tId" in the raw player data
        team_id = None
        if raw_players_data:
            team_id = _get_player_team_id_from_raw(pid_int, raw_players_data)
        
        # Get stats from local cache (popupstats directory)
        # Load directly from popupstats file for full data
        stats = None
        popup_path = BASE_DIR / "popupstats" / f"popupstats_80_{pid_int}.json"
        if popup_path.exists():
            try:
                with open(popup_path, 'r', encoding='utf-8') as f:
                    raw_data = json.load(f)
                    # Extract data section if wrapped
                    if isinstance(raw_data, dict) and "data" in raw_data:
                        stats = raw_data.get("data", {})
                    else:
                        stats = raw_data
            except Exception as e:
                pass
        
        # Fallback to cached if file doesn't exist
        if not isinstance(stats, dict) or not stats:
            stats = get_player_stats_cached(pid_int)
        
        if not isinstance(stats, dict) or not stats:
            continue
        
        # Get points for this MD using _ucl_points_for_md
        # This function properly extracts points from matchdayPoints array
        md_stats = _ucl_points_for_md(stats, md)
        if not md_stats:
            # If no MD-specific stats, skip player (they didn't play in this MD)
            continue
        
        points = _safe_int(md_stats.get("tPoints"))
        
        # Get team ID from stats if not found in player data
        if not team_id:
            team_id = _resolve_team_id_from_stats(stats)
        
        # Get club name
        club_name = _resolve_club_name_from_stats(stats, player)
        
        # Normalize position
        pos = _normalize_position(player.get("position"))
        if pos not in pos_limits:
            continue
        
        # Convert matchdays set to list for JSON serialization
        matchdays_list = list(matchdays) if isinstance(matchdays, set) else matchdays
        
        available_players.append({
            "playerId": pid_int,
            "fullName": player.get("fullName") or player.get("name") or str(pid_int),
            "name": player.get("fullName") or player.get("name") or str(pid_int),
            "position": pos,
            "pos": pos,
            "clubName": club_name,
            "club": club_name,
            "teamId": str(team_id) if team_id else None,
            "points": points,
            "matchdays": matchdays_list,
        })
    
    # Sort by points descending
    available_players.sort(key=lambda x: x["points"], reverse=True)
    
    # Build team using greedy algorithm
    selected: List[Dict[str, Any]] = []
    pos_counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    clubs_used: Set[str] = set()
    total_points = 0
    
    for player in available_players:
        pos = player["pos"]
        club = (player.get("club") or "").upper()
        
        # Check position limit
        if pos_counts[pos] >= pos_limits[pos]:
            continue
        
        # Check club limit (one player per club)
        if club and club in clubs_used:
            continue
        
        # Add player (ensure matchdays is a list, not a set)
        player_copy = dict(player)
        if "matchdays" in player_copy and isinstance(player_copy["matchdays"], set):
            player_copy["matchdays"] = sorted(list(player_copy["matchdays"]))
        selected.append(player_copy)
        pos_counts[pos] += 1
        if club:
            clubs_used.add(club)
        total_points += player["points"]
        
        # Check if team is complete (25 players total)
        if sum(pos_counts.values()) >= 25:
            break
    
    return {
        "players": selected,
        "total": total_points,
    }

def main():
    print("=" * 80)
    print("ПРЕДВАРИТЕЛЬНЫЙ РАСЧЕТ ОПТИМАЛЬНЫХ СБОРНЫХ UCL")
    print("=" * 80)
    
    # Load state
    state = _ucl_state_load()
    if not state:
        print("❌ Ошибка: не удалось загрузить draft_state_ucl.json")
        return
    
    # Clear old optimal_teams to ensure fresh calculation
    if "optimal_teams" in state:
        old_count = len(state["optimal_teams"])
        print(f"⚠️  Очистка старых данных optimal_teams (было {old_count} MD)")
        state["optimal_teams"] = {}
    
    # Load players
    raw_players = _json_load(UCL_PLAYERS)
    if not raw_players:
        print("❌ Ошибка: не удалось загрузить players_80_en_1.json")
        print(f"   Путь: {UCL_PLAYERS}")
        return
    
    all_players = _players_from_ucl(raw_players)
    print(f"✅ Загружено игроков: {len(all_players)}")
    
    # Get managers and rosters
    rosters = state.get("rosters", {})
    managers = [m for m in UCL_PARTICIPANTS if m in rosters]
    print(f"✅ Менеджеров: {len(managers)}")
    
    # Keep raw players data for teamId extraction
    raw_players_data = raw_players
    
    # Initialize optimal_teams in state if not exists
    if "optimal_teams" not in state:
        state["optimal_teams"] = {}
    
    # Calculate for MD 1-6
    results_summary = []
    
    for md in range(1, 7):
        print(f"\n📊 Расчет MD{md}...")
        
        # Team of The MD (all players)
        print(f"  • Team of The MD...")
        team_of_md = build_optimal_team_for_md(
            all_players, md, managers, rosters, exclude_picked=False, raw_players_data=raw_players_data
        )
        # Verify total points by summing player points
        calculated_total = sum(p.get("points", 0) for p in team_of_md["players"])
        print(f"    Игроков: {len(team_of_md['players'])}, Баллов: {calculated_total}")
        
        # Непикнутые гении (unpicked only)
        print(f"  • Непикнутые гении...")
        unpicked_geniuses = build_optimal_team_for_md(
            all_players, md, managers, rosters, exclude_picked=True, raw_players_data=raw_players_data
        )
        
        # Verify total points by summing player points
        calculated_total_unp = sum(p.get("points", 0) for p in unpicked_geniuses["players"])
        print(f"    Игроков: {len(unpicked_geniuses['players'])}, Баллов: {calculated_total_unp}")
        
        # Update totals to match calculated values
        team_of_md["total"] = calculated_total
        unpicked_geniuses["total"] = calculated_total_unp
        
        # Save to state (save in same format as expected by ucl_lineups_data)
        state["optimal_teams"][str(md)] = {
            "team_of_md": {
                "players": team_of_md["players"],
                "total": team_of_md["total"],
            },
            "unpicked_geniuses": {
                "players": unpicked_geniuses["players"],
                "total": unpicked_geniuses["total"],
            },
        }
        print(f"    ✅ Сохранено в state для MD{md}: {len(team_of_md['players'])} игроков, {team_of_md['total']} баллов")
        
        results_summary.append({
            "md": md,
            "team_of_md": {
                "players_count": len(team_of_md["players"]),
                "total": team_of_md["total"],
            },
            "unpicked_geniuses": {
                "players_count": len(unpicked_geniuses["players"]),
                "total": unpicked_geniuses["total"],
            },
        })
    
    # Print summary
    print("\n" + "=" * 80)
    print("ИТОГОВАЯ СВОДКА ПО БАЛЛАМ")
    print("=" * 80)
    print(f"\n{'MD':<5} {'Team of The MD':<30} {'Непикнутые гении':<30}")
    print("-" * 80)
    for r in results_summary:
        print(f"{r['md']:<5} {r['team_of_md']['total']:>6} баллов ({r['team_of_md']['players_count']:>2} игр.)  {r['unpicked_geniuses']['total']:>6} баллов ({r['unpicked_geniuses']['players_count']:>2} игр.)")
    
    total_tom = sum(r["team_of_md"]["total"] for r in results_summary)
    total_unp = sum(r["unpicked_geniuses"]["total"] for r in results_summary)
    print("-" * 80)
    print(f"{'ИТОГО':<5} {total_tom:>6} баллов              {total_unp:>6} баллов")
    print("=" * 80)
    
    # Save state directly to file (bypass S3 to avoid conflicts)
    print(f"\n💾 Сохранение данных...")
    print(f"   В state перед сохранением: {len(state.get('optimal_teams', {}))} MD")
    
    try:
        # Save directly using atomic write with error handling
        tmp = UCL_STATE.with_suffix(UCL_STATE.suffix + ".tmp")
        json_str = json.dumps(state, ensure_ascii=False, indent=2)
        tmp.write_text(json_str, encoding="utf-8")
        tmp.replace(UCL_STATE)
        print(f"✅ Оптимальные сборные сохранены в draft_state_ucl.json")
        
        # Verify save by reading file directly (not through _ucl_state_load which might use S3)
        verify_state = _json_load(UCL_STATE) or {}
        verify_teams = verify_state.get("optimal_teams", {})
        print(f"✅ Проверка: сохранено {len(verify_teams)} MD")
        for md_str in sorted(verify_teams.keys(), key=int):
            teams = verify_teams[md_str]
            tom = teams.get("team_of_md", {})
            unp = teams.get("unpicked_geniuses", {})
            players_count_tom = len(tom.get("players", []))
            players_count_unp = len(unp.get("players", []))
            print(f"   MD{md_str}: Team of MD={tom.get('total', 0)} ({players_count_tom} игр.), Unpicked={unp.get('total', 0)} ({players_count_unp} игр.)")
    except Exception as e:
        print(f"❌ Ошибка при сохранении: {e}")
        import traceback
        traceback.print_exc()
    
    print("=" * 80)

if __name__ == "__main__":
    main()

