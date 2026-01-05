"""
API Football client for Top-4 draft data
Uses api-football.com API (v3)
"""
from __future__ import annotations
import os
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime

# API Football configuration
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "db093905c47e849c124fd69a9d94df57")

# League IDs for Top-4 leagues (EPL, La Liga, Serie A, Bundesliga)
# Season 2024-2025
LEAGUE_IDS = {
    "EPL": 39,  # Premier League
    "La Liga": 140,  # La Liga
    "Serie A": 135,  # Serie A
    "Bundesliga": 78,  # Bundesliga
}

# Position mapping from API Football to Top-4
POSITION_MAP = {
    "Goalkeeper": "GK",
    "Defender": "DEF",
    "Midfielder": "MID",
    "Attacker": "FWD",
}


class APIFootballClient:
    """Client for API Football (api-football.com)"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or API_FOOTBALL_KEY
        self.base_url = API_FOOTBALL_BASE_URL
        self.headers = {
            "x-apisports-key": self.api_key,
            "x-rapidapi-host": "v3.football.api-sports.io",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make API request with error handling"""
        url = f"{self.base_url}/{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # API Football returns data in 'response' field
            if "response" in data:
                return data["response"]
            return data
        except requests.exceptions.RequestException as e:
            print(f"API Football request error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    print(f"Error response: {error_data}")
                except:
                    print(f"Error status: {e.response.status_code}")
            return None
        except Exception as e:
            print(f"Unexpected error in API Football request: {e}")
            return None
    
    def get_leagues(self, season: int = 2024) -> List[Dict]:
        """Get all leagues for a season"""
        params = {"season": season}
        return self._make_request("leagues", params) or []
    
    def get_teams(self, league_id: int, season: int = 2024) -> List[Dict]:
        """Get teams for a league"""
        params = {
            "league": league_id,
            "season": season,
        }
        return self._make_request("teams", params) or []
    
    def get_players(self, league_id: int, season: int = 2024, team_id: Optional[int] = None) -> List[Dict]:
        """Get players for a league (optionally filtered by team)"""
        params = {
            "league": league_id,
            "season": season,
        }
        if team_id:
            params["team"] = team_id
        
        return self._make_request("players", params) or []
    
    def get_player_statistics(self, player_id: int, league_id: int, season: int = 2024) -> Optional[Dict]:
        """Get detailed statistics for a player"""
        params = {
            "player": player_id,
            "league": league_id,
            "season": season,
        }
        result = self._make_request("players", params)
        if result and len(result) > 0:
            return result[0]
        return None
    
    def get_fixtures(self, league_id: int, season: int = 2024, round: Optional[str] = None) -> List[Dict]:
        """Get fixtures for a league"""
        params = {
            "league": league_id,
            "season": season,
        }
        if round:
            params["round"] = round
        
        return self._make_request("fixtures", params) or []
    
    def get_standings(self, league_id: int, season: int = 2024) -> Optional[Dict]:
        """Get standings for a league"""
        params = {
            "league": league_id,
            "season": season,
        }
        result = self._make_request("standings", params)
        if result and len(result) > 0:
            return result[0]
        return None
    
    def get_all_top4_players(self, season: int = 2024) -> List[Dict]:
        """Get all players from Top-4 leagues"""
        all_players = []
        seen_player_ids = set()  # Track unique players across leagues
        
        for league_name, league_id in LEAGUE_IDS.items():
            print(f"Fetching players from {league_name} (league_id={league_id})...")
            players = self.get_players(league_id, season)
            
            for player_data in players:
                if "player" in player_data and "statistics" in player_data:
                    player_info = player_data["player"]
                    player_id = player_info.get("id")
                    
                    # Skip duplicates (same player in multiple leagues)
                    if player_id in seen_player_ids:
                        continue
                    seen_player_ids.add(player_id)
                    
                    stats_list = player_data["statistics"]
                    
                    # Get stats from first team (usually current team)
                    stats = stats_list[0] if stats_list else {}
                    team_info = stats.get("team", {})
                    
                    # Format player for Top-4 draft
                    formatted_player = {
                        "api_football_id": player_id,
                        "name": player_info.get("name", ""),
                        "firstname": player_info.get("firstname", ""),
                        "lastname": player_info.get("lastname", ""),
                        "age": player_info.get("age"),
                        "birth": player_info.get("birth", {}),
                        "nationality": player_info.get("nationality"),
                        "height": player_info.get("height"),
                        "weight": player_info.get("weight"),
                        "injured": player_info.get("injured", False),
                        "photo": player_info.get("photo"),
                        "position": self._normalize_position(stats.get("games", {}).get("position", "")),
                        "league": league_name,
                        "league_id": league_id,
                        "team": {
                            "id": team_info.get("id"),
                            "name": team_info.get("name", ""),
                            "logo": team_info.get("logo"),
                        },
                        "statistics": self._format_statistics(stats),
                        "season": season,
                    }
                    all_players.append(formatted_player)
            
            print(f"  Found {len(players)} player records from {league_name}")
        
        print(f"Total unique players from Top-4 leagues: {len(all_players)}")
        return all_players
    
    def _normalize_position(self, position: str) -> str:
        """Normalize position from API Football to Top-4 format"""
        if not position:
            return "MID"  # Default
        
        position_lower = position.lower()
        if "goalkeeper" in position_lower or position_lower == "gk":
            return "GK"
        elif "defender" in position_lower or position_lower == "def":
            return "DEF"
        elif "midfielder" in position_lower or position_lower == "mid":
            return "MID"
        elif "attacker" in position_lower or position_lower == "att" or position_lower == "fwd":
            return "FWD"
        else:
            # Try to map from POSITION_MAP
            for api_pos, top4_pos in POSITION_MAP.items():
                if api_pos.lower() in position_lower:
                    return top4_pos
            return "MID"  # Default fallback
    
    def _format_statistics(self, stats: Dict) -> Dict:
        """Format statistics from API Football format"""
        games = stats.get("games", {})
        goals = stats.get("goals", {})
        cards = stats.get("cards", {})
        shots = stats.get("shots", {})
        passes = stats.get("passes", {})
        tackles = stats.get("tackles", {})
        duels = stats.get("duels", {})
        dribbles = stats.get("dribbles", {})
        fouls = stats.get("fouls", {})
        
        return {
            "games": {
                "appearences": games.get("appearences", 0),
                "lineups": games.get("lineups", 0),
                "minutes": games.get("minutes", 0),
                "position": games.get("position", ""),
                "rating": games.get("rating", "0.0"),
                "captain": games.get("captain", False),
            },
            "goals": {
                "total": goals.get("total", 0),
                "conceded": goals.get("conceded", 0),
                "assists": goals.get("assists", 0),
                "saves": goals.get("saves", 0),
            },
            "cards": {
                "yellow": cards.get("yellow", 0),
                "red": cards.get("red", 0),
            },
            "shots": {
                "total": shots.get("total", 0),
                "on": shots.get("on", 0),
            },
            "passes": {
                "total": passes.get("total", 0),
                "key": passes.get("key", 0),
                "accuracy": passes.get("accuracy", "0"),
            },
            "tackles": {
                "total": tackles.get("total", 0),
                "blocks": tackles.get("blocks", 0),
                "interceptions": tackles.get("interceptions", 0),
            },
            "duels": {
                "total": duels.get("total", 0),
                "won": duels.get("won", 0),
            },
            "dribbles": {
                "attempts": dribbles.get("attempts", 0),
                "success": dribbles.get("success", 0),
            },
            "fouls": {
                "drawn": fouls.get("drawn", 0),
                "committed": fouls.get("committed", 0),
            },
        }
    
    def format_player_for_draft(self, player_data: Dict) -> Dict:
        """Format player data for Top-4 draft system"""
        stats = player_data.get("statistics", {})
        team = player_data.get("team", {})
        
        # Calculate fantasy points (simplified formula)
        # This should be adjusted based on actual Top-4 scoring rules
        fantasy_points = self._calculate_fantasy_points(stats)
        
        return {
            "playerId": str(player_data.get("api_football_id", "")),
            "name": player_data.get("name", ""),
            "position": player_data.get("position", "MID"),
            "club": team.get("name", ""),
            "league": player_data.get("league", ""),
            "price": 0.0,  # API Football doesn't provide prices
            "popularity": fantasy_points,
            "api_football_data": player_data,
            "source": "api-football",
        }
    
    def _calculate_fantasy_points(self, stats: Dict) -> float:
        """Calculate fantasy points from statistics"""
        games = stats.get("games", {})
        goals = stats.get("goals", {})
        cards = stats.get("cards", {})
        
        points = 0.0
        
        # Basic scoring (adjust based on actual Top-4 rules)
        points += games.get("appearences", 0) * 2  # Appearance points
        points += goals.get("total", 0) * 4  # Goal points
        points += goals.get("assists", 0) * 3  # Assist points
        points -= cards.get("yellow", 0) * 1  # Yellow card penalty
        points -= cards.get("red", 0) * 3  # Red card penalty
        
        # Clean sheet bonus for goalkeepers and defenders
        # (would need more detailed stats for this)
        
        return round(points, 1)


# Global instance
api_football_client = APIFootballClient()

