#!/usr/bin/env python3
"""
Скрипт для построения оптимальных сборных для закрытых matchdays UCL
и сохранения их в draft_state_ucl.json
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from draft_app.ucl import (
    _json_load,
    _json_dump_atomic,
    _players_from_ucl,
    _player_matchdays,
    _ucl_state_load,
    _ucl_state_save,
    UCL_PLAYERS,
    UCL_STATE,
    UCL_PARTICIPANTS,
    get_player_stats_cached,
    _ucl_points_for_md,
    _coerce_matchday,
)

def _norm_team_id(raw: Any) -> Optional[str]:
    if raw in (None, "", [], {}):
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        num = int(float(text))
        if num <= 0:
            return None
        return str(num)
    except Exception:
        return text or None

def _first_non_empty(*values: Any) -> Optional[str]:
    for val in values:
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)) and val:
            return str(val)
    return None

def _safe_int(value: Any) -> int:
    """Safely convert value to int"""
    if value is None:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0

def _stat_sections(stats_payload: Any, md: int) -> tuple:
    stat_payload_result: Dict[str, Any] = {}
    points_dict: Dict[str, Any] = {}
    raw_stats: Dict[str, Any] = {}
    data_section: Dict[str, Any] = {}
    value_section: Dict[str, Any] = {}

    if isinstance(stats_payload, dict):
        stat_payload_result = _ucl_points_for_md(stats_payload, md) or {}
        if isinstance(stat_payload_result.get("points"), dict):
            points_dict = stat_payload_result.get("points") or {}
        if isinstance(stat_payload_result.get("stats"), dict):
            raw_stats = stat_payload_result.get("stats") or {}

        if isinstance(stat_payload_result.get("data"), dict):
            data_section = stat_payload_result.get("data") or {}
            if isinstance(data_section.get("value"), dict):
                value_section = data_section.get("value") or {}
        if not value_section and isinstance(stat_payload_result.get("value"), dict):
            value_section = stat_payload_result.get("value") or {}

    return stat_payload_result, points_dict, raw_stats, data_section, value_section

def _resolve_team_id(
    payload: Dict[str, Any],
    stat_payload: Dict[str, Any],
    points_dict: Dict[str, Any],
    raw_stats: Dict[str, Any],
    data_section: Dict[str, Any],
    value_section: Dict[str, Any],
    full_stats: Any,
) -> Optional[str]:
    base_stats = full_stats if isinstance(full_stats, dict) else {}
    stats_data = base_stats.get("data") if isinstance(base_stats.get("data"), dict) else {}
    root_value = base_stats.get("value") if isinstance(base_stats.get("value"), dict) else {}

    for candidate in (
        stat_payload.get("teamId"),
        stat_payload.get("tId"),
        raw_stats.get("teamId"),
        raw_stats.get("tId"),
        points_dict.get("teamId"),
        points_dict.get("tId"),
        value_section.get("teamId"),
        value_section.get("teamID"),
        data_section.get("teamId"),
        data_section.get("teamID"),
        root_value.get("teamId"),
        root_value.get("teamID"),
        base_stats.get("teamId"),
        base_stats.get("teamID"),
        stats_data.get("teamId"),
        stats_data.get("teamID"),
        payload.get("teamId"),
        payload.get("clubId"),
    ):
        norm = _norm_team_id(candidate)
        if norm:
            return norm
    return None

def build_optimal_team_for_md(
    all_players: List[Dict[str, Any]],
    md: int,
    rosters: Dict[str, List[Dict[str, Any]]],
    managers: List[str],
    exclude_picked: bool = False,
) -> Dict[str, Any]:
    """Build optimal team for a specific MD"""
    # Position limits: GK: 3, DEF: 8, MID: 9, FWD: 5
    pos_limits = {"GK": 3, "DEF": 8, "MID": 9, "FWD": 5}
    
    # Get picked player IDs for this MD
    picked_ids_for_md: Set[int] = set()
    if exclude_picked:
        for manager in managers:
            roster = rosters.get(manager, [])
            for item in roster:
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
    
    # Filter players
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
        
        # Get stats
        stats = get_player_stats_cached(pid_int)
        if not stats:
            # Skip if no stats available
            continue
        stat_payload, points_dict, raw_stats, data_section, value_section = _stat_sections(stats, md)
        points = _safe_int(stat_payload.get("tPoints"))
        
        # Include players even with 0 points if they played in this MD
        
        # Get team ID and club name
        team_id = _resolve_team_id(player, stat_payload, points_dict, raw_stats, data_section, value_section, stats)
        club_name = _first_non_empty(
            player.get("clubName"),
            stat_payload.get("teamName"),
            stat_payload.get("tName"),
            raw_stats.get("teamName"),
            points_dict.get("teamName"),
        )
        
        pos_raw = player.get("position")
        # Normalize position
        pos = None
        if pos_raw:
            pos_upper = str(pos_raw).upper()
            if pos_upper.startswith('GOAL') or pos_upper in ('GK', 'GKP'):
                pos = 'GK'
            elif pos_upper.startswith('DEF'):
                pos = 'DEF'
            elif pos_upper.startswith('MID'):
                pos = 'MID'
            elif pos_upper.startswith('FWD') or pos_upper.startswith('FOR'):
                pos = 'FWD'
        
        if pos not in pos_limits:
            continue
        
        available_players.append({
            "playerId": pid_int,
            "fullName": player.get("fullName") or player.get("name") or str(pid_int),
            "position": pos,
            "clubName": club_name,
            "teamId": team_id,
            "points": points,
            "matchdays": matchdays,
        })
    
    # Sort by points descending
    available_players.sort(key=lambda x: x["points"], reverse=True)
    
    # Build team using greedy algorithm
    selected: List[Dict[str, Any]] = []
    pos_counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    clubs_used: Set[str] = set()
    total_points = 0
    
    for player in available_players:
        pos = player["position"]
        club = (player.get("clubName") or "").upper()
        
        # Check position limit
        if pos_counts[pos] >= pos_limits[pos]:
            continue
        
        # Check club limit (one player per club)
        if club and club in clubs_used:
            continue
        
        # Add player
        selected.append(player)
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
    print("ПОСТРОЕНИЕ ОПТИМАЛЬНЫХ СБОРНЫХ ДЛЯ UCL")
    print("=" * 80)
    
    # Load state
    state = _ucl_state_load()
    rosters = state.get("rosters", {})
    managers = [m for m in UCL_PARTICIPANTS if m in rosters]
    if not managers:
        managers = sorted(rosters.keys())
    
    # Get finished matchdays
    finished_matchdays = state.get("finished_matchdays", [])
    print(f"\nЗавершенные туры: {finished_matchdays}")
    
    # Load all players
    print("\nЗагрузка всех игроков...")
    raw_players = _json_load(UCL_PLAYERS) or []
    all_ucl_players = _players_from_ucl(raw_players)
    print(f"Загружено игроков: {len(all_ucl_players)}")
    
    # Initialize optimal_teams in state if not exists
    if "optimal_teams" not in state:
        state["optimal_teams"] = {}
    
    # Build teams for MD 1-6
    for md in range(1, 7):
        if md not in finished_matchdays:
            print(f"\n⚠️  MD{md} еще не завершен, пропускаем")
            continue
        
        print(f"\n{'=' * 80}")
        print(f"MD{md}")
        print(f"{'=' * 80}")
        
        # Build Team of The MD
        print("Построение 'Team of The MD'...")
        team_of_md = build_optimal_team_for_md(
            all_ucl_players, md, rosters, managers, exclude_picked=False
        )
        print(f"  Игроков: {len(team_of_md['players'])}")
        print(f"  Всего баллов: {team_of_md['total']}")
        
        # Build Непикнутые гении
        print("Построение 'Непикнутые гении'...")
        unpicked_geniuses = build_optimal_team_for_md(
            all_ucl_players, md, rosters, managers, exclude_picked=True
        )
        print(f"  Игроков: {len(unpicked_geniuses['players'])}")
        print(f"  Всего баллов: {unpicked_geniuses['total']}")
        
        # Save to state
        state["optimal_teams"][str(md)] = {
            "team_of_md": team_of_md,
            "unpicked_geniuses": unpicked_geniuses,
        }
    
    # Save state
    print(f"\n{'=' * 80}")
    print("Сохранение в draft_state_ucl.json...")
    _ucl_state_save(state)
    print("✅ Готово!")
    print(f"{'=' * 80}")

if __name__ == "__main__":
    main()

