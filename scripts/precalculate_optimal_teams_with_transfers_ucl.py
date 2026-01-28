#!/usr/bin/env python3
"""
Pre-calculate optimal teams with 1 transfer per matchday rule for UCL draft.
Saves results to draft_state_ucl.json in optimal_teams_with_transfers section.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Set, Optional

# Add parent directory to path
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.ucl import (
    UCL_STATE,
    UCL_PLAYERS,
    UCL_PARTICIPANTS,
    UCL_TOTAL_MATCHDAYS,
    _json_load,
    _ucl_state_load,
    _ucl_state_save,
    _players_from_ucl,
    _player_matchdays,
    _get_all_ucl_clubs,
)
from draft_app.ucl_stats_store import get_player_stats_cached
from draft_app.ucl import _ucl_points_for_md, _safe_int


def _normalize_position(pos_raw: Any) -> Optional[str]:
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


def _get_player_club(player: Dict[str, Any], pid: int, md: int) -> str:
    """Get player club name"""
    stats = get_player_stats_cached(pid)
    if isinstance(stats, dict):
        md_stat = _ucl_points_for_md(stats, md)
        if md_stat:
            return md_stat.get("tName") or md_stat.get("teamName") or player.get("clubName") or ""
    return player.get("clubName") or ""


def build_optimal_team_with_transfers(
    finished_mds: List[int],
    rosters: Dict[str, List[Dict[str, Any]]],
    managers: List[str],
    exclude_picked: bool = False
) -> Dict[str, Any]:
    """
    Build optimal team with rule: 1 transfer allowed between matchdays.
    Algorithm considers total points across all remaining MDs when making transfers.
    """
    pos_limits = {"GK": 3, "DEF": 8, "MID": 9, "FWD": 5}
    
    # Load all players
    raw_players = _json_load(UCL_PLAYERS) or []
    all_ucl_players = _players_from_ucl(raw_players)
    
    # Get picked player IDs for each MD
    def get_roster_for_md(manager: str, target_md: int) -> List[Dict]:
        """Get manager's roster as it was for the specific MD"""
        current_roster = list(rosters.get(manager, []))
        # Simplified version - just return current roster
        # In production, this should rollback transfers properly
        return current_roster
    
    picked_ids_by_md: Dict[int, Set[int]] = {}
    if exclude_picked:
        for md in finished_mds:
            picked_ids: Set[int] = set()
            for manager in managers:
                try:
                    roster = get_roster_for_md(manager, md)
                    for item in roster:
                        payload = item.get("player") if isinstance(item, dict) and item.get("player") else item
                        if isinstance(payload, dict):
                            pid = payload.get("playerId")
                            if pid:
                                try:
                                    picked_ids.add(int(pid))
                                except Exception:
                                    pass
                except Exception:
                    pass
            picked_ids_by_md[md] = picked_ids
    
    # Helper to get player points for a specific MD
    def get_player_points_for_md(pid: int, md: int) -> int:
        stats = get_player_stats_cached(pid)
        if not isinstance(stats, dict):
            return 0
        md_stats = _ucl_points_for_md(stats, md)
        if not md_stats:
            return 0
        return _safe_int(md_stats.get("tPoints", 0))
    
    # Build initial team for MD1
    if not finished_mds:
        return {"players": [], "total": 0, "available_clubs": []}
    
    first_md = finished_mds[0]
    current_team: List[Dict[str, Any]] = []
    current_clubs: Set[str] = set()
    current_pos_counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    # Track which matchdays each player was in the team
    # Key: player_id, Value: list of MDs when player was in team
    player_matchdays_in_team: Dict[int, List[int]] = {}
    # Track all players who were ever in the team (including transferred out)
    all_players_in_team_history: List[Dict[str, Any]] = []
    
    # Get available players for MD1
    available_for_md1 = []
    for player in all_ucl_players:
        pid = player.get("playerId")
        if not pid:
            continue
        try:
            pid_int = int(pid)
        except Exception:
            continue
        
        # Skip if exclude_picked and player is picked
        if exclude_picked and pid_int in picked_ids_by_md.get(first_md, set()):
            continue
        
        # Check if player played in MD1
        matchdays = _player_matchdays(player)
        if first_md not in matchdays:
            continue
        
        pos = _normalize_position(player.get("position"))
        if pos not in pos_limits:
            continue
        
        points = get_player_points_for_md(pid_int, first_md)
        club = _get_player_club(player, pid_int, first_md).upper()
        
        available_for_md1.append({
            "playerId": pid_int,
            "fullName": player.get("fullName") or player.get("name") or str(pid_int),
            "name": player.get("fullName") or player.get("name") or str(pid_int),
            "pos": pos,
            "club": club,
            "points": points,
            "player": player,
        })
    
    # Sort by points descending
    available_for_md1.sort(key=lambda x: x["points"], reverse=True)
    
    # Build initial team (greedy algorithm)
    for player in available_for_md1:
        pos = player["pos"]
        club = player["club"]
        
        if current_pos_counts[pos] >= pos_limits[pos]:
            continue
        if club and club in current_clubs:
            continue
        
        current_team.append(player)
        current_pos_counts[pos] += 1
        if club:
            current_clubs.add(club)
        # Track that this player starts in team from MD1
        # Will be updated if player is transferred out later
        player_matchdays_in_team[player["playerId"]] = finished_mds.copy()  # Start with all MDs, will be trimmed on transfer
        
        if sum(current_pos_counts.values()) >= 25:
            break
    
    # Now iterate through remaining MDs and make transfers if beneficial
    # We consider total points across all remaining MDs when making transfers
    transfers_made = 0
    for md_idx, md in enumerate(finished_mds[1:], start=1):
        # Remaining MDs after this one (including current)
        remaining_mds = finished_mds[md_idx:]
        
        # Find best transfer: replace one player with another (same position)
        # Consider total improvement across all remaining MDs
        best_total_improvement = 0
        best_out_player = None
        best_in_player = None
        
        # Get available players for remaining MDs
        available_for_md = []
        for player in all_ucl_players:
            pid = player.get("playerId")
            if not pid:
                continue
            try:
                pid_int = int(pid)
            except Exception:
                continue
            
            # Skip if exclude_picked and player is picked in any remaining MD
            if exclude_picked:
                is_picked = any(pid_int in picked_ids_by_md.get(m, set()) for m in remaining_mds)
                if is_picked:
                    continue
            
            # Check if player played in at least one remaining MD
            matchdays = _player_matchdays(player)
            if not any(m in matchdays for m in remaining_mds):
                continue
            
            pos = _normalize_position(player.get("position"))
            if pos not in pos_limits:
                continue
            
            # Calculate total points across all remaining MDs
            total_points = sum(get_player_points_for_md(pid_int, m) for m in remaining_mds)
            club = _get_player_club(player, pid_int, md).upper()
            
            available_for_md.append({
                "playerId": pid_int,
                "fullName": player.get("fullName") or player.get("name") or str(pid_int),
                "name": player.get("fullName") or player.get("name") or str(pid_int),
                "pos": pos,
                "club": club,
                "points": total_points,  # Total points across all remaining MDs
                "player": player,
            })
        
        # Try each player in current team as candidate for transfer out
        for out_idx, out_player in enumerate(current_team):
            out_pos = out_player["pos"]
            
            # Calculate total points that out_player will give in remaining MDs
            out_total_points = sum(get_player_points_for_md(out_player["playerId"], m) for m in remaining_mds)
            
            # Try each available player as candidate for transfer in (same position)
            for in_player in available_for_md:
                if in_player["pos"] != out_pos:
                    continue
                
                # Check if in_player is already in team
                if any(p["playerId"] == in_player["playerId"] for p in current_team):
                    continue
                
                # Check club limit
                in_club = in_player["club"]
                if in_club and in_club in current_clubs:
                    # Check if we're removing the only player from this club
                    out_club = out_player["club"]
                    if out_club != in_club:
                        continue  # Can't add, club already used
                
                # Calculate total improvement across all remaining MDs
                in_total_points = in_player["points"]
                total_improvement = in_total_points - out_total_points
                
                if total_improvement > best_total_improvement:
                    best_total_improvement = total_improvement
                    best_out_player = (out_idx, out_player)
                    best_in_player = in_player
        
        # Make transfer if beneficial
        if best_total_improvement > 0 and best_out_player and best_in_player:
            transfers_made += 1
            out_idx, out_player = best_out_player
            out_pid = out_player["playerId"]
            in_pid = best_in_player["playerId"]
            
            # Update matchdays tracking: out_player was in team until md-1, in_player starts from md
            if out_pid in player_matchdays_in_team:
                # out_player was in team from start until md-1 (inclusive)
                # Keep only MDs before current md (md-1 and earlier)
                player_matchdays_in_team[out_pid] = [m for m in finished_mds if m < md]
            # in_player starts from current md (inclusive)
            player_matchdays_in_team[in_pid] = [m for m in finished_mds if m >= md]
            
            # Remove old player's club if no other player from that club
            out_club = out_player["club"]
            if out_club and not any(p["club"] == out_club for i, p in enumerate(current_team) if i != out_idx):
                current_clubs.discard(out_club)
            
            # Add new player
            current_team[out_idx] = best_in_player
            in_club = best_in_player["club"]
            if in_club:
                current_clubs.add(in_club)
        elif md_idx == 1:  # Debug first MD transfer attempt
            print(f"    [DEBUG] MD{md}: No transfer made. best_improvement={best_total_improvement}, candidates checked")
    
    # Calculate total points across all finished MDs
    # We need to count points for ALL players who were ever in the team (including transferred out)
    # Build complete list: current team + all players who were transferred out
    all_players_ever_in_team: Dict[int, Dict[str, Any]] = {}
    
    # Add all current players
    for player in current_team:
        pid = player["playerId"]
        all_players_ever_in_team[pid] = player
    
    # Add all players from history (those who were transferred out)
    # We can find them by checking player_matchdays_in_team for players not in current_team
    current_team_pids = {p["playerId"] for p in current_team}
    for pid, mds_list in player_matchdays_in_team.items():
        if pid not in current_team_pids and mds_list:
            # This player was transferred out, need to find their data
            # Search in all_ucl_players
            for p in all_ucl_players:
                if p.get("playerId") == pid:
                    pos = _normalize_position(p.get("position"))
                    club = _get_player_club(p, pid, finished_mds[0]).upper()
                    all_players_ever_in_team[pid] = {
                        "playerId": pid,
                        "fullName": p.get("fullName") or p.get("name") or str(pid),
                        "name": p.get("fullName") or p.get("name") or str(pid),
                        "pos": pos,
                        "club": club,
                        "player": p,
                    }
                    break
    
    # Calculate total points: sum points for each MD, counting only players who were in team that MD
    total_points = 0
    md_breakdown = {}  # For debugging
    for md in finished_mds:
        md_points = 0
        players_in_md = []
        for pid, player in all_players_ever_in_team.items():
            mds_in_team = player_matchdays_in_team.get(pid, [])
            if md in mds_in_team:
                points = get_player_points_for_md(pid, md)
                md_points += points
                players_in_md.append((pid, points))
        total_points += md_points
        md_breakdown[md] = {"points": md_points, "players": len(players_in_md)}
    
    # Debug: print breakdown if total seems wrong
    if exclude_picked:
        debug_label = "Непикнутые"
    else:
        debug_label = "Optimal Team"
    if total_points < 300:  # Suspiciously low
        print(f"    [DEBUG {debug_label}] Total: {total_points}, Breakdown by MD:")
        for md, info in sorted(md_breakdown.items()):
            print(f"      MD{md}: {info['points']} pts ({info['players']} players)")
        print(f"    [DEBUG {debug_label}] Players in team history: {len(all_players_ever_in_team)}")
        print(f"    [DEBUG {debug_label}] Current team size: {len(current_team)}")
        print(f"    [DEBUG {debug_label}] Transfers made: {transfers_made}")
        # Show sample player matchdays
        print(f"    [DEBUG {debug_label}] Sample player matchdays (first 3):")
        for i, (pid, mds) in enumerate(list(player_matchdays_in_team.items())[:3]):
            print(f"      Player {pid}: MDs {mds}")
    
    # Format players for output (only current team, but with correct points)
    formatted_players = []
    all_clubs = _get_all_ucl_clubs()
    
    for player in current_team:
        pid = player["playerId"]
        stats = get_player_stats_cached(pid)
        team_id = None
        if isinstance(stats, dict):
            md_stat = _ucl_points_for_md(stats, finished_mds[0] if finished_mds else 1)
            if md_stat:
                team_id = md_stat.get("tId") or md_stat.get("teamId")
        
        # Calculate total points only for MDs when player was in team
        mds_in_team = player_matchdays_in_team.get(pid, finished_mds)  # Default to all MDs if not tracked
        player_total = sum(get_player_points_for_md(pid, md) for md in mds_in_team if md in finished_mds)
        
        formatted_players.append({
            "playerId": pid,
            "fullName": player["fullName"],
            "name": player["name"],
            "position": player["pos"],
            "pos": player["pos"],
            "clubName": player["club"],
            "club": player["club"],
            "teamId": str(team_id) if team_id else None,
            "points": player_total,
            "matchdays": sorted(mds_in_team),  # Only MDs when player was in team
        })
    
    # Calculate unused clubs
    used_clubs = {p["club"] for p in formatted_players if p["club"]}
    unused_clubs = [
        {"clubName": name, "teamId": info.get("teamId")}
        for name, info in all_clubs.items()
        if name.upper() not in used_clubs
    ]
    
    return {
        "players": formatted_players,
        "total": total_points,
        "available_clubs": unused_clubs
    }


def main():
    print("=" * 80)
    print("ПРЕДВАРИТЕЛЬНЫЙ РАСЧЕТ ОПТИМАЛЬНЫХ СБОРНЫХ С ТРАНСФЕРАМИ UCL")
    print("=" * 80)
    
    # Load state (from S3 if enabled, otherwise local file)
    state = _ucl_state_load()
    if not state:
        print("❌ Не удалось загрузить draft_state_ucl.json")
        return
    
    # Get finished matchdays
    finished_matchdays = state.get("finished_matchdays", [])
    if not finished_matchdays:
        print("⚠️  Нет завершенных туров")
        return
    
    finished_mds_list = sorted(list(finished_matchdays))
    print(f"✅ Завершенные туры: {finished_mds_list}")
    
    # Get managers and rosters
    rosters = state.get("rosters", {})
    managers = [m for m in UCL_PARTICIPANTS if m in rosters]
    if not managers:
        managers = sorted(rosters.keys())
    print(f"✅ Менеджеров: {len(managers)}")
    
    # Initialize optimal_teams_with_transfers in state if not exists
    if "optimal_teams_with_transfers" not in state:
        state["optimal_teams_with_transfers"] = {}
    
    # Clear old data
    print(f"⚠️  Очистка старых данных optimal_teams_with_transfers")
    state["optimal_teams_with_transfers"] = {}
    
    # Build optimal teams
    print(f"\n📊 Расчет оптимальной сборной с трансферами...")
    
    # Optimal Team (all players)
    print(f"  • Optimal Team (1 трансфер/тур)...")
    optimal_with_transfers = build_optimal_team_with_transfers(
        finished_mds_list, rosters, managers, exclude_picked=False
    )
    calculated_total = sum(p.get("points", 0) for p in optimal_with_transfers["players"])
    stored_total = optimal_with_transfers.get("total", 0)
    print(f"    Игроков: {len(optimal_with_transfers['players'])}, Баллов (сумма игроков): {calculated_total}, Баллов (stored): {stored_total}")
    if calculated_total != stored_total:
        print(f"    ⚠️  РАСХОЖДЕНИЕ! Сумма очков игроков ({calculated_total}) != stored total ({stored_total})")
        # Update stored total to match calculated
        optimal_with_transfers["total"] = calculated_total
    
    # Непикнутые гении
    print(f"  • Непикнутые (1 трансфер/тур)...")
    optimal_unpicked = build_optimal_team_with_transfers(
        finished_mds_list, rosters, managers, exclude_picked=True
    )
    calculated_total_unpicked = sum(p.get("points", 0) for p in optimal_unpicked["players"])
    stored_total_unpicked = optimal_unpicked.get("total", 0)
    print(f"    Игроков: {len(optimal_unpicked['players'])}, Баллов (сумма игроков): {calculated_total_unpicked}, Баллов (stored): {stored_total_unpicked}")
    if calculated_total_unpicked != stored_total_unpicked:
        print(f"    ⚠️  РАСХОЖДЕНИЕ! Сумма очков игроков ({calculated_total_unpicked}) != stored total ({stored_total_unpicked})")
        print(f"    ✅ Исправляю: устанавливаю total = {calculated_total_unpicked}")
        # Update stored total to match calculated (sum of player points is the source of truth)
        optimal_unpicked["total"] = calculated_total_unpicked
    
    # Save to state
    state["optimal_teams_with_transfers"] = {
        "optimal_team": optimal_with_transfers,
        "unpicked_geniuses": optimal_unpicked
    }
    
    # Save state (to S3 if enabled, otherwise local file)
    print(f"\n💾 Сохранение данных...")
    _ucl_state_save(state)
    print(f"✅ Оптимальные сборные с трансферами сохранены в draft_state_ucl.json (и на S3 если настроено)")
    
    # Verify
    verify_state = _ucl_state_load()
    if verify_state and "optimal_teams_with_transfers" in verify_state:
        saved_data = verify_state["optimal_teams_with_transfers"]
        opt_team = saved_data.get("optimal_team", {})
        unp_team = saved_data.get("unpicked_geniuses", {})
        print(f"✅ Проверка: сохранено")
        print(f"   Optimal Team: {opt_team.get('total', 0)} баллов ({len(opt_team.get('players', []))} игр.)")
        print(f"   Непикнутые: {unp_team.get('total', 0)} баллов ({len(unp_team.get('players', []))} игр.)")
    
    print("=" * 80)


if __name__ == "__main__":
    main()

