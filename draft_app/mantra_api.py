"""
MantraFootball API integration module
"""
import requests
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
import difflib
import re

class MantraFootballAPI:
    BASE_URL = "https://mantrafootball.org/api"
    
    # Tournament IDs for TOP-4 leagues
    TOURNAMENT_IDS = {
        'italy': 1,
        'england': 2, 
        'germany': 3,
        'spain': 5
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'accept': 'application/json',
            'User-Agent': 'DraftApp/1.0'
        })
    
    def get_tournaments(self, include_clubs=True) -> Dict[str, Any]:
        """Get all tournaments with optional clubs data"""
        url = f"{self.BASE_URL}/tournaments"
        params = {'clubs': 'true'} if include_clubs else {}
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching tournaments: {e}")
            return {"data": []}
    
    def get_top4_tournaments(self) -> List[Dict[str, Any]]:
        """Get tournaments for TOP-4 leagues (Italy, England, Germany, Spain)"""
        all_tournaments = self.get_tournaments(include_clubs=True)
        top4_tournaments = []
        
        for tournament in all_tournaments.get('data', []):
            if tournament['id'] in self.TOURNAMENT_IDS.values():
                top4_tournaments.append(tournament)
        
        return top4_tournaments
    
    def get_players(self, filters: Dict[str, Any] = None, page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """Get players with optional filters"""
        url = f"{self.BASE_URL}/players"
        params = {
            'page[number]': page,
            'page[size]': page_size
        }
        
        if filters:
            for key, value in filters.items():
                if isinstance(value, list):
                    params[f'filter[{key}][]'] = value
                else:
                    params[f'filter[{key}]'] = value
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching players: {e}")
            return {"data": []}
    
    def get_all_top4_players(self) -> List[Dict[str, Any]]:
        """Get all players from TOP-4 tournaments"""
        all_players = []
        tournament_ids = list(self.TOURNAMENT_IDS.values())
        
        # Get players page by page for each tournament
        for tournament_id in tournament_ids:
            page = 1
            while True:
                filters = {'tournament_id': [tournament_id]}
                result = self.get_players(filters=filters, page=page, page_size=100)
                
                players = result.get('data', [])
                if not players:
                    break
                
                all_players.extend(players)
                page += 1
                
                # Safety check to avoid infinite loops
                if page > 100:  # Max 10,000 players per tournament
                    break
        
        return all_players
    
    def get_player_details(self, player_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed player information"""
        url = f"{self.BASE_URL}/players/{player_id}"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json().get('data')
        except requests.RequestException as e:
            print(f"Error fetching player {player_id}: {e}")
            return None
    
    def get_player_stats(self, player_id: int) -> Optional[Dict[str, Any]]:
        """Get player statistics"""
        url = f"{self.BASE_URL}/players/{player_id}/stats"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json().get('data')
        except requests.RequestException as e:
            print(f"Error fetching player stats {player_id}: {e}")
            return None


class PlayerMatcher:
    """Class for automatic player matching between draft and MantraFootball data"""
    
    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize player name for matching"""
        if not name:
            return ""
        
        # Remove accents and special characters
        name = name.lower().strip()
        
        # Common name replacements
        replacements = {
            'á': 'a', 'à': 'a', 'ä': 'a', 'â': 'a', 'ã': 'a',
            'é': 'e', 'è': 'e', 'ë': 'e', 'ê': 'e',
            'í': 'i', 'ì': 'i', 'ï': 'i', 'î': 'i',
            'ó': 'o', 'ò': 'o', 'ö': 'o', 'ô': 'o', 'õ': 'o',
            'ú': 'u', 'ù': 'u', 'ü': 'u', 'û': 'u',
            'ñ': 'n', 'ç': 'c',
            'ß': 'ss'
        }
        
        for old, new in replacements.items():
            name = name.replace(old, new)
        
        # Remove non-alphabetic characters except spaces and hyphens
        name = re.sub(r'[^a-z\s\-]', '', name)
        
        return name
    
    @staticmethod
    def normalize_club_name(club_name: str) -> str:
        """Normalize club name for matching"""
        if not club_name:
            return ""
        
        club_name = club_name.lower().strip()
        
        # Common club name replacements
        replacements = {
            'fc': '',
            'f.c.': '',
            'ac': '',
            'a.c.': '',
            'sc': '',
            's.c.': '',
            'cf': '',
            'c.f.': '',
            'real': '',
            'atletico': 'atletico',
            'athletic': 'athletic'
        }
        
        for old, new in replacements.items():
            club_name = club_name.replace(old, new)
        
        # Remove extra spaces
        club_name = ' '.join(club_name.split())
        
        return club_name
    
    @staticmethod
    def calculate_name_similarity(name1: str, name2: str) -> float:
        """Calculate similarity between two names"""
        norm1 = PlayerMatcher.normalize_name(name1)
        norm2 = PlayerMatcher.normalize_name(name2)
        
        if not norm1 or not norm2:
            return 0.0
        
        return difflib.SequenceMatcher(None, norm1, norm2).ratio()
    
    @staticmethod
    def calculate_club_similarity(club1: str, club2: str) -> float:
        """Calculate similarity between two club names"""
        norm1 = PlayerMatcher.normalize_club_name(club1)
        norm2 = PlayerMatcher.normalize_club_name(club2)
        
        if not norm1 or not norm2:
            return 0.0
        
        return difflib.SequenceMatcher(None, norm1, norm2).ratio()
    
    @staticmethod
    def find_best_match(draft_player: Dict[str, Any], mantra_players: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Find the best matching MantraFootball player for a draft player"""
        best_match = None
        best_score = 0.0
        
        draft_name = draft_player.get('name', '')
        draft_club = draft_player.get('club', {}).get('name', '') if draft_player.get('club') else ''
        
        for mantra_player in mantra_players:
            mantra_name = mantra_player.get('name', '')
            mantra_club = mantra_player.get('club', {}).get('name', '') if mantra_player.get('club') else ''
            
            # Calculate name similarity
            name_similarity = PlayerMatcher.calculate_name_similarity(draft_name, mantra_name)
            
            # Calculate club similarity
            club_similarity = PlayerMatcher.calculate_club_similarity(draft_club, mantra_club)
            
            # Combined score (name is more important than club)
            combined_score = (name_similarity * 0.7) + (club_similarity * 0.3)
            
            if combined_score > best_score and combined_score > 0.6:  # Minimum threshold
                best_score = combined_score
                best_match = {
                    'mantra_player': mantra_player,
                    'similarity_score': combined_score,
                    'name_similarity': name_similarity,
                    'club_similarity': club_similarity
                }
        
        return best_match


def format_mantra_player_for_draft(mantra_player: Dict[str, Any], stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convert MantraFootball player data to draft format"""
    club = mantra_player.get('club', {})
    
    # Map positions from MantraFootball to draft format
    position_mapping = {
        'GK': 'GK',
        'CB': 'DEF', 'LB': 'DEF', 'RB': 'DEF', 'LWB': 'DEF', 'RWB': 'DEF',
        'CM': 'MID', 'CDM': 'MID', 'CAM': 'MID', 'LM': 'MID', 'RM': 'MID',
        'LW': 'FWD', 'RW': 'FWD', 'CF': 'FWD', 'ST': 'FWD'
    }
    
    # Get primary position
    positions = mantra_player.get('position_classic_arr', [])
    primary_position = positions[0] if positions else 'MID'
    draft_position = position_mapping.get(primary_position, 'MID')
    
    # Calculate price based on stats or use default
    base_price = 5.0
    if stats and stats.get('current_season_stat'):
        current_stats = stats['current_season_stat']
        total_score = current_stats.get('total_score', 0)
        base_score = current_stats.get('base_score', 0)
        appearances = current_stats.get('played_matches', 0)
        
        # Simple price calculation based on performance
        if total_score and appearances > 5:
            base_price = min(max(total_score * 2, 1.0), 15.0)
    
    return {
        'id': f"mantra_{mantra_player['id']}",
        'mantra_id': mantra_player['id'],
        'name': mantra_player['name'],
        'first_name': mantra_player.get('first_name', ''),
        'position': draft_position,
        'positions': [draft_position],  # Could be expanded with all positions
        'club': {
            'id': club.get('id'),
            'name': club.get('name', ''),
            'code': club.get('code', ''),
            'logo_path': club.get('logo_path', '')
        },
        'price': base_price,
        'avatar_path': mantra_player.get('avatar_path', ''),
        'stats': {
            'appearances': mantra_player.get('appearances', 0),
            'total_score': stats.get('current_season_stat', {}).get('total_score', 0) if stats else 0,
            'base_score': stats.get('current_season_stat', {}).get('base_score', 0) if stats else 0,
            'goals': stats.get('current_season_stat', {}).get('goals', 0) if stats else 0,
            'assists': stats.get('current_season_stat', {}).get('assists', 0) if stats else 0,
        },
        'mantra_data': mantra_player,
        'last_updated': datetime.utcnow().isoformat()
    }
