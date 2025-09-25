"""
MantraFootball API integration module
"""
import requests
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
import difflib
import re
import unicodedata


# Transliteration mapping for Russian to English
RUSSIAN_TO_ENGLISH = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'Yo', 'Ж': 'Zh',
    'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M', 'Н': 'N', 'О': 'O',
    'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F', 'Х': 'H', 'Ц': 'Ts',
    'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Sch', 'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya'
}

# Common club name translations
CLUB_NAME_TRANSLATIONS = {
    'Реал Мадрид': 'Real Madrid',
    'Барселона': 'Barcelona',
    'Атлетико': 'Atletico Madrid',
    'Валенсия': 'Valencia',
    'Севилья': 'Sevilla',
    'Бетис': 'Real Betis',
    'Райо Вальекано': 'Rayo Vallecano',
    'Хетафе': 'Getafe',
    'Эспаньол': 'Espanyol',
    'Атлетик': 'Athletic Bilbao',
    'Реал Сосьедад': 'Real Sociedad',
    'Осасуна': 'Osasuna',
    'Сельта': 'Celta Vigo',
    'Вильярреал': 'Villarreal',
    'Алавес': 'Alaves',
    'Леванте': 'Levante',
    'Эльче': 'Elche',
    'Мальорка': 'Mallorca',
    'Манчестер Сити': 'Manchester City',
    'Манчестер Юнайтед': 'Manchester United',
    'Ливерпуль': 'Liverpool',
    'Челси': 'Chelsea',
    'Арсенал': 'Arsenal',
    'Тоттенхэм': 'Tottenham',
    'Ньюкасл': 'Newcastle',
    'Астон Вилла': 'Aston Villa',
    'Вест Хэм': 'West Ham',
    'Лидс': 'Leeds United',
    'Эвертон': 'Everton',
    'Лестер': 'Leicester City',
    'Кристал Пэлас': 'Crystal Palace',
    'Брайтон': 'Brighton',
    'Бернли': 'Burnley',
    'Саутгемптон': 'Southampton',
    'Ноттингем Форест': 'Nottingham Forest',
    'Фулхэм': 'Fulham',
    'Борнмут': 'Bournemouth',
    'Брентфорд': 'Brentford',
    'Вулверхэмптон': 'Wolverhampton',
    'Бавария': 'Bayern Munich',
    'Боруссия Д': 'Borussia Dortmund',
    'Боруссия М': 'Borussia Monchengladbach',
    'РБ Лейпциг': 'RB Leipzig',
    'Байер': 'Bayer Leverkusen',
    'Вольфсбург': 'Wolfsburg',
    'Айнтрахт Ф': 'Eintracht Frankfurt',
    'Штутгарт': 'Stuttgart',
    'Фрайбург': 'Freiburg',
    'Хоффенхайм': 'Hoffenheim',
    'Майнц': 'Mainz',
    'Кельн': 'Cologne',
    'Аугсбург': 'Augsburg',
    'Унион Берлин': 'Union Berlin',
    'Хайденхайм': '1. FC Heidenheim',
    'Санкт-Паули': 'St. Pauli',
    'Вердер': 'Werder Bremen',
    'Гамбург': 'Hamburg',
    'Ювентус': 'Juventus',
    'Милан': 'AC Milan',
    'Интер': 'Inter Milan',
    'Наполи': 'Napoli',
    'Рома': 'AS Roma',
    'Лацио': 'Lazio',
    'Аталанта': 'Atalanta',
    'Фиорентина': 'Fiorentina',
    'Торино': 'Torino',
    'Болонья': 'Bologna',
    'Сассуоло': 'Sassuolo',
    'Удинезе': 'Udinese',
    'Верона': 'Hellas Verona',
    'Дженоа': 'Genoa',
    'Лечче': 'Lecce',
    'Парма': 'Parma',
    'Кальяри': 'Cagliari',
    'Венеция': 'Venezia',
    'Комо': 'Como',
    'Пиза': 'Pisa'
}


def transliterate_russian_to_english(text: str) -> str:
    """Transliterate Russian text to English"""
    if not text:
        return ""
    
    result = ""
    for char in text:
        result += RUSSIAN_TO_ENGLISH.get(char, char)
    
    return result


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert value to int, handling strings and None"""
    if value is None:
        return default
    try:
        return int(float(value))  # Convert via float to handle string floats like "5.0"
    except (ValueError, TypeError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert value to float, handling strings and None"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

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
        """Normalize player name for matching (supports Russian and English)"""
        if not name:
            return ""
        
        # Debug: log transliteration for first few calls
        original_name = name
        
        # First transliterate Russian to English if needed
        name = transliterate_russian_to_english(name)
        
        # Convert to lowercase and strip
        name = name.lower().strip()
        
        # Debug: show transliteration result for Russian names (commented out for performance)
        # if original_name != name and any(ord(c) > 127 for c in original_name):
        #     print(f"[Transliteration] '{original_name}' -> '{name}'")
        
        # Common name replacements for various languages
        replacements = {
            'á': 'a', 'à': 'a', 'ä': 'a', 'â': 'a', 'ã': 'a', 'å': 'a',
            'é': 'e', 'è': 'e', 'ë': 'e', 'ê': 'e',
            'í': 'i', 'ì': 'i', 'ï': 'i', 'î': 'i',
            'ó': 'o', 'ò': 'o', 'ö': 'o', 'ô': 'o', 'õ': 'o', 'ø': 'o',
            'ú': 'u', 'ù': 'u', 'ü': 'u', 'û': 'u',
            'ñ': 'n', 'ç': 'c', 'ß': 'ss',
            'đ': 'd', 'ð': 'd', 'þ': 'th',
            'æ': 'ae', 'œ': 'oe'
        }
        
        for old, new in replacements.items():
            name = name.replace(old, new)
        
        # Remove non-alphabetic characters except spaces and hyphens
        name = re.sub(r'[^a-z\s\-]', '', name)
        
        # Remove extra spaces
        name = re.sub(r'\s+', ' ', name).strip()
        
        return name
    
    @staticmethod
    def normalize_club_name(club_name: str) -> str:
        """Normalize club name for matching (supports Russian and English)"""
        if not club_name:
            return ""
        
        # Debug: log club name processing
        original_club = club_name
        
        # First check for direct translation
        if club_name in CLUB_NAME_TRANSLATIONS:
            club_name = CLUB_NAME_TRANSLATIONS[club_name]
            # print(f"[ClubTranslation] '{original_club}' -> '{club_name}' (direct)")  # Commented out for performance
        
        # Transliterate Russian to English if needed
        club_name = transliterate_russian_to_english(club_name)
        
        # Debug: show transliteration result for Russian club names (commented out for performance)
        # if original_club != club_name and any(ord(c) > 127 for c in original_club) and original_club not in CLUB_NAME_TRANSLATIONS:
        #     print(f"[ClubTransliteration] '{original_club}' -> '{club_name}'")
        
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
        """Calculate similarity between two names with advanced matching"""
        norm1 = PlayerMatcher.normalize_name(name1)
        norm2 = PlayerMatcher.normalize_name(name2)
        
        if not norm1 or not norm2:
            return 0.0
        
        # Exact match
        if norm1 == norm2:
            return 1.0
        
        # Split names into parts
        parts1 = norm1.split()
        parts2 = norm2.split()
        
        # Try different matching strategies
        scores = []
        
        # 1. Full string similarity
        scores.append(difflib.SequenceMatcher(None, norm1, norm2).ratio())
        
        # 2. Word-by-word matching (for first name + last name)
        if len(parts1) >= 2 and len(parts2) >= 2:
            # Match first and last names
            first_sim = difflib.SequenceMatcher(None, parts1[0], parts2[0]).ratio()
            last_sim = difflib.SequenceMatcher(None, parts1[-1], parts2[-1]).ratio()
            word_score = (first_sim + last_sim) / 2
            scores.append(word_score)
        
        # 3. Substring matching (for partial names)
        if len(parts1) >= 1 and len(parts2) >= 1:
            # Check if any part of one name is contained in the other
            substring_scores = []
            for p1 in parts1:
                for p2 in parts2:
                    if len(p1) >= 3 and len(p2) >= 3:  # Only for meaningful parts
                        if p1 in p2 or p2 in p1:
                            substring_scores.append(0.8)  # High score for substring match
                        else:
                            substring_scores.append(difflib.SequenceMatcher(None, p1, p2).ratio())
            
            if substring_scores:
                scores.append(max(substring_scores))
        
        # 4. Initials matching (for cases like "M. Salah" vs "Mohamed Salah")
        if len(parts1) >= 2 and len(parts2) >= 2:
            initials1 = ''.join(p[0] for p in parts1 if p)
            initials2 = ''.join(p[0] for p in parts2 if p)
            if initials1 == initials2:
                scores.append(0.7)  # Good score for matching initials
        
        # Return the best score
        return max(scores) if scores else 0.0
    
    @staticmethod
    def calculate_club_similarity(club1: str, club2: str) -> float:
        """Calculate similarity between two club names with improved logic"""
        if not club1 or not club2:
            return 0.0
        
        # Check for exact translation match first
        # This prevents Leeds-Lecce confusion
        if club1 in CLUB_NAME_TRANSLATIONS and CLUB_NAME_TRANSLATIONS[club1].lower() == club2.lower():
            return 1.0  # Perfect match through translation
        if club2 in CLUB_NAME_TRANSLATIONS and CLUB_NAME_TRANSLATIONS[club2].lower() == club1.lower():
            return 1.0  # Perfect match through translation
        
        # Check for partial translation matches (e.g., "Верона" -> "Hellas Verona" should match "Verona")
        if club1 in CLUB_NAME_TRANSLATIONS:
            translated = CLUB_NAME_TRANSLATIONS[club1].lower()
            if club2.lower() in translated or translated in club2.lower():
                # Check if it's a meaningful partial match (not just random substring)
                if len(club2) >= 4 and club2.lower() in translated:
                    return 1.0  # "Verona" matches "Hellas Verona"
        if club2 in CLUB_NAME_TRANSLATIONS:
            translated = CLUB_NAME_TRANSLATIONS[club2].lower()
            if club1.lower() in translated or translated in club1.lower():
                # Check if it's a meaningful partial match
                if len(club1) >= 4 and club1.lower() in translated:
                    return 1.0  # "Verona" matches "Hellas Verona"
        
        # Check for exact match after normalization
        norm1 = PlayerMatcher.normalize_club_name(club1)
        norm2 = PlayerMatcher.normalize_club_name(club2)
        
        if not norm1 or not norm2:
            return 0.0
        
        # Exact match after normalization
        if norm1 == norm2:
            return 1.0
        
        # Special cases for known problematic pairs
        problematic_pairs = [
            ('everton', 'verona'),
            ('everton', 'hellas verona'),
            ('leeds', 'lecce'),
        ]
        
        norm1_clean = norm1.replace(' ', '').lower()
        norm2_clean = norm2.replace(' ', '').lower()
        
        for pair1, pair2 in problematic_pairs:
            pair1_clean = pair1.replace(' ', '')
            pair2_clean = pair2.replace(' ', '')
            if (norm1_clean == pair1_clean and norm2_clean == pair2_clean) or (norm1_clean == pair2_clean and norm2_clean == pair1_clean):
                return 0.3  # Force low similarity for known problematic pairs
        
        # For similar but different clubs, use multiple similarity checks
        sequence_similarity = difflib.SequenceMatcher(None, norm1, norm2).ratio()
        
        # Additional check: if the clubs are very short and similar, be more careful
        if len(norm1) <= 6 and len(norm2) <= 6 and abs(len(norm1) - len(norm2)) <= 1:
            # For short club names, require higher similarity to avoid confusion
            if sequence_similarity < 0.8:
                return sequence_similarity * 0.5  # Reduce similarity for ambiguous short names
        
        return sequence_similarity
    
    @staticmethod
    def find_best_match(draft_player: Dict[str, Any], mantra_players: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Find the best matching MantraFootball player for a draft player"""
        best_match = None
        best_score = 0.0
        
        draft_name = draft_player.get('name', '')
        
        # Handle both string and dict club formats
        draft_club_data = draft_player.get('club', '')
        if isinstance(draft_club_data, dict):
            draft_club = draft_club_data.get('name', '')
        else:
            draft_club = str(draft_club_data) if draft_club_data else ''
        
        for mantra_player in mantra_players:
            # Safely extract mantra player data
            if not isinstance(mantra_player, dict):
                continue
                
            mantra_name = mantra_player.get('name', '')
            
            # Handle both string and dict club formats for mantra players too
            mantra_club_data = mantra_player.get('club', '')
            if isinstance(mantra_club_data, dict):
                mantra_club = mantra_club_data.get('name', '')
            else:
                mantra_club = str(mantra_club_data) if mantra_club_data else ''
            
            # Calculate name similarity
            name_similarity = PlayerMatcher.calculate_name_similarity(draft_name, mantra_name)
            
            # Calculate club similarity
            club_similarity = PlayerMatcher.calculate_club_similarity(draft_club, mantra_club)
            
            # Combined score (name is more important than club)
            combined_score = (name_similarity * 0.7) + (club_similarity * 0.3)
            
            if combined_score > best_score and combined_score > 0.4:  # Lowered threshold for more matches
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
        
        # Safely convert to numbers
        try:
            total_score = float(current_stats.get('total_score', 0) or 0)
            base_score = float(current_stats.get('base_score', 0) or 0)
            appearances = int(current_stats.get('played_matches', 0) or 0)
        except (ValueError, TypeError):
            total_score = 0.0
            base_score = 0.0
            appearances = 0
        
        # Simple price calculation based on performance
        if total_score > 0 and appearances > 5:
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
            'appearances': _safe_int(mantra_player.get('appearances', 0)),
            'total_score': _safe_float(stats.get('current_season_stat', {}).get('total_score', 0) if stats else 0),
            'base_score': _safe_float(stats.get('current_season_stat', {}).get('base_score', 0) if stats else 0),
            'goals': _safe_int(stats.get('current_season_stat', {}).get('goals', 0) if stats else 0),
            'assists': _safe_int(stats.get('current_season_stat', {}).get('assists', 0) if stats else 0),
        },
        'mantra_data': mantra_player,
        'last_updated': datetime.utcnow().isoformat()
    }
