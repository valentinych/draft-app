"""
Converter for API Football statistics to Top-4 score calculation format
This module converts API Football player statistics to the format expected by _calc_score_breakdown
"""
from __future__ import annotations
from typing import Dict, Any, Optional


def convert_api_football_stats_to_top4_format(
    api_football_stats: Dict[str, Any],
    position: str,
    round_no: Optional[int] = None,
    fixture_data: Optional[Dict[str, Any]] = None,
    team_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Convert API Football statistics to Top-4 score calculation format.
    
    This function converts statistics from API Football API format to the format
    expected by _calc_score_breakdown in mantra_routes.py.
    
    CRITICAL: Clean Sheet calculation
    - Clean Sheet = team didn't concede goals in the match AND player played >= 60 minutes
    - For GK, DEF: 4 points if clean sheet
    - For MID: 1 point if clean sheet
    - Clean sheet is determined by checking if team conceded 0 goals in the fixture
    
    Args:
        api_football_stats: Statistics from API Football (from _format_statistics)
        position: Player position (GK, DEF, MID, FWD)
        round_no: Optional round number for per-round statistics
        fixture_data: Optional fixture data to determine clean sheet
        team_id: Optional team ID to check clean sheet for
    
    Returns:
        Dictionary in format expected by _calc_score_breakdown
    """
    games = api_football_stats.get("games", {})
    goals = api_football_stats.get("goals", {})
    cards = api_football_stats.get("cards", {})
    
    # Extract key values
    minutes = games.get("minutes", 0)
    goals_total = goals.get("total", 0)
    assists = goals.get("assists", 0)
    goals_conceded = goals.get("conceded", 0)  # Goals conceded by team (for GK/DEF)
    saves = goals.get("saves", 0)
    yellow_cards = cards.get("yellow", 0)
    red_cards = cards.get("red", 0)
    
    # CRITICAL: Calculate Clean Sheet correctly
    # Clean Sheet = team didn't concede goals in THIS match AND player played >= 60 minutes
    cleansheet = False
    if position in ("GK", "DEF", "MID"):
        # For per-match statistics, we need fixture data to determine clean sheet
        if fixture_data and team_id:
            # Use fixture data to determine if team had clean sheet
            cleansheet = get_clean_sheet_from_api_football_fixture(fixture_data, team_id)
        else:
            # Fallback: use goals_conceded from season stats
            # This is less accurate but works if we don't have fixture data
            # NOTE: This assumes goals_conceded is for the specific match, which may not be true
            # For accurate clean sheet calculation, fixture_data should be provided
            if goals_conceded == 0 and minutes >= 60:
                cleansheet = True
        
        # Only count clean sheet if player played at least 60 minutes
        if cleansheet and minutes < 60:
            cleansheet = False
    
    # Build the stat dictionary in Top-4 format
    # This format matches what _calc_score_breakdown expects
    stat = {
        "played_minutes": int(minutes),
        "goals": float(goals_total),
        "assists": float(assists),
        "cleansheet": bool(cleansheet),  # CRITICAL: Clean Sheet flag (must be boolean)
        "saves": int(saves),  # For goalkeepers
        "missed_goals": int(goals_conceded),  # For GK and DEF penalty calculation (-1 per 2 goals)
        "yellow_card": int(yellow_cards),
        "red_card": int(red_cards),
        # Additional fields that might be needed
        "scored_penalty": 0,  # API Football doesn't provide this separately in basic stats
        "missed_penalty": 0,  # API Football doesn't provide this separately in basic stats
        "caught_penalty": 0,  # API Football doesn't provide this separately in basic stats
    }
    
    return stat


def convert_api_football_player_data_for_round(
    player_data: Dict[str, Any],
    round_no: int
) -> Dict[str, Any]:
    """
    Convert API Football player data for a specific round.
    
    This function extracts round-specific statistics from API Football data
    and converts them to Top-4 format.
    
    Args:
        player_data: Full player data from API Football (from get_all_top4_players)
        round_no: Round number to extract statistics for
    
    Returns:
        Dictionary in format expected by _calc_score_breakdown for the specific round
    """
    # API Football provides season statistics, not per-round
    # We need to fetch fixture-specific statistics for the round
    # For now, we'll use season statistics as a fallback
    
    stats = player_data.get("statistics", {})
    position = player_data.get("position", "MID")
    
    # Convert statistics
    stat = convert_api_football_stats_to_top4_format(stats, position, round_no)
    
    return stat


def get_clean_sheet_from_api_football_fixture(
    fixture_data: Dict[str, Any],
    team_id: int
) -> bool:
    """
    Determine if a team had a clean sheet in a specific fixture.
    
    Args:
        fixture_data: Fixture data from API Football
        team_id: Team ID to check clean sheet for
    
    Returns:
        True if team had clean sheet (didn't concede), False otherwise
    """
    # Extract goals conceded by the team
    home_team_id = fixture_data.get("teams", {}).get("home", {}).get("id")
    away_team_id = fixture_data.get("teams", {}).get("away", {}).get("id")
    
    home_goals = fixture_data.get("goals", {}).get("home")
    away_goals = fixture_data.get("goals", {}).get("away")
    
    if team_id == home_team_id:
        # Team is home, check if away team scored
        return away_goals == 0
    elif team_id == away_team_id:
        # Team is away, check if home team scored
        return home_goals == 0
    
    return False

