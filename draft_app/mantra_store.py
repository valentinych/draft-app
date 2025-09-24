"""
MantraFootball data storage and synchronization
"""
import json
import os
import boto3
from typing import Dict, List, Optional, Any
from datetime import datetime
from .mantra_api import MantraFootballAPI, PlayerMatcher, format_mantra_player_for_draft
from .top4_player_info_store import save_player_info

class MantraDataStore:
    def __init__(self):
        self.api = MantraFootballAPI()
        self.s3_client = None
        self.bucket_name = os.environ.get('AWS_S3_BUCKET')
        
        if self.bucket_name:
            try:
                self.s3_client = boto3.client('s3')
            except Exception as e:
                print(f"Failed to initialize S3 client: {e}")
    
    def get_local_cache_path(self, cache_type: str) -> str:
        """Get local cache file path"""
        cache_dir = "data/cache/mantra_data"
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"{cache_type}.json")
    
    def get_s3_key(self, cache_type: str) -> str:
        """Get S3 key for cache file"""
        return f"mantra_data/{cache_type}.json"
    
    def save_to_local_cache(self, data: Any, cache_type: str):
        """Save data to local cache"""
        cache_path = self.get_local_cache_path(cache_type)
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"Saved {cache_type} data to local cache: {cache_path}")
        except Exception as e:
            print(f"Error saving to local cache: {e}")
    
    def load_from_local_cache(self, cache_type: str) -> Optional[Any]:
        """Load data from local cache"""
        cache_path = self.get_local_cache_path(cache_type)
        try:
            if os.path.exists(cache_path):
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading from local cache: {e}")
        return None
    
    def save_to_s3(self, data: Any, cache_type: str):
        """Save data to S3"""
        if not self.s3_client or not self.bucket_name:
            return
        
        s3_key = self.get_s3_key(cache_type)
        try:
            json_data = json.dumps(data, ensure_ascii=False, indent=2)
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=json_data.encode('utf-8'),
                ContentType='application/json'
            )
            print(f"Saved {cache_type} data to S3: s3://{self.bucket_name}/{s3_key}")
        except Exception as e:
            print(f"Error saving to S3: {e}")
    
    def load_from_s3(self, cache_type: str) -> Optional[Any]:
        """Load data from S3"""
        if not self.s3_client or not self.bucket_name:
            return None
        
        s3_key = self.get_s3_key(cache_type)
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            data = json.loads(response['Body'].read().decode('utf-8'))
            return data
        except Exception as e:
            print(f"Error loading from S3: {e}")
            return None
    
    def save_data(self, data: Any, cache_type: str):
        """Save data to both local cache and S3"""
        self.save_to_local_cache(data, cache_type)
        self.save_to_s3(data, cache_type)
    
    def load_data(self, cache_type: str) -> Optional[Any]:
        """Load data from local cache first, then S3"""
        # Try local cache first
        data = self.load_from_local_cache(cache_type)
        if data:
            return data
        
        # Try S3 if local cache is empty
        data = self.load_from_s3(cache_type)
        if data:
            # Save to local cache for faster access
            self.save_to_local_cache(data, cache_type)
        
        return data
    
    def sync_tournaments(self) -> Dict[str, Any]:
        """Sync TOP-4 tournaments data"""
        print("Syncing tournaments data from MantraFootball...")
        
        tournaments = self.api.get_top4_tournaments()
        
        sync_data = {
            'tournaments': tournaments,
            'last_updated': datetime.utcnow().isoformat(),
            'total_tournaments': len(tournaments)
        }
        
        self.save_data(sync_data, 'tournaments')
        return sync_data
    
    def sync_players(self, include_stats: bool = False) -> Dict[str, Any]:
        """Sync all TOP-4 players data"""
        print("Syncing players data from MantraFootball...")
        
        all_players = self.api.get_all_top4_players()
        formatted_players = []
        
        print(f"Found {len(all_players)} players from TOP-4 leagues")
        
        for i, player in enumerate(all_players):
            if i % 50 == 0:
                print(f"Processing player {i+1}/{len(all_players)}")
            
            stats = None
            if include_stats:
                stats = self.api.get_player_stats(player['id'])
            
            formatted_player = format_mantra_player_for_draft(player, stats)
            formatted_players.append(formatted_player)
            
            # Save individual player info file to top4_player_info/ and S3
            player_id = formatted_player.get('mantra_id')  # Use mantra_id instead of playerId
            if player_id:
                try:
                    # Create player info structure compatible with existing system
                    player_info = {
                        'id': player_id,
                        'name': formatted_player.get('name', ''),
                        'club': formatted_player.get('club', {}).get('name', ''),
                        'position': formatted_player.get('position', ''),
                        'league': 'TOP4',  # All MantraFootball players are TOP-4
                        'price': formatted_player.get('price', 0.0),
                        'popularity': formatted_player.get('stats', {}).get('total_score', 0.0),
                        'mantra_data': formatted_player.get('mantra_data', {}),
                        'stats': stats if include_stats else None,
                        'synced_at': datetime.utcnow().isoformat(),
                        'source': 'MantraFootball'
                    }
                    save_player_info(int(player_id), player_info)
                    
                    if i % 100 == 0 and i > 0:
                        print(f"Saved {i} player info files to top4_player_info/")
                        
                except Exception as e:
                    print(f"Error saving player info for {player_id}: {e}")
                    continue
        
        # Final summary
        saved_count = len([p for p in formatted_players if p.get('mantra_id')])
        print(f"Completed: Saved {saved_count} player info files to top4_player_info/ and S3")
        
        sync_data = {
            'players': formatted_players,
            'last_updated': datetime.utcnow().isoformat(),
            'total_players': len(formatted_players),
            'saved_player_files': saved_count,
            'include_stats': include_stats
        }
        
        self.save_data(sync_data, 'players')
        return sync_data
    
    def sync_player_stats(self, player_ids: List[int]) -> Dict[str, Any]:
        """Sync specific player statistics"""
        print(f"Syncing stats for {len(player_ids)} players...")
        
        stats_data = {}
        for i, player_id in enumerate(player_ids):
            if i % 10 == 0:
                print(f"Processing stats {i+1}/{len(player_ids)}")
            
            stats = self.api.get_player_stats(player_id)
            if stats:
                stats_data[str(player_id)] = stats
        
        sync_data = {
            'player_stats': stats_data,
            'last_updated': datetime.utcnow().isoformat(),
            'total_stats': len(stats_data)
        }
        
        self.save_data(sync_data, 'player_stats')
        return sync_data
    
    def get_tournaments(self) -> List[Dict[str, Any]]:
        """Get cached tournaments data"""
        data = self.load_data('tournaments')
        return data.get('tournaments', []) if data else []
    
    def get_players(self) -> List[Dict[str, Any]]:
        """Get cached players data"""
        data = self.load_data('players')
        return data.get('players', []) if data else []
    
    def get_player_stats(self, player_id: int) -> Optional[Dict[str, Any]]:
        """Get cached player stats"""
        data = self.load_data('player_stats')
        if data and data.get('player_stats'):
            return data['player_stats'].get(str(player_id))
        return None
    
    def match_draft_players_with_mantra(self, draft_players: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Match draft players with MantraFootball data"""
        print("Matching draft players with MantraFootball data...")
        
        mantra_players = self.get_players()
        if not mantra_players:
            print("No MantraFootball players data available. Please sync first.")
            return {'matches': [], 'unmatched': draft_players}
        
        matches = []
        unmatched = []
        
        for draft_player in draft_players:
            match = PlayerMatcher.find_best_match(draft_player, mantra_players)
            
            if match:
                matches.append({
                    'draft_player': draft_player,
                    'mantra_player': match['mantra_player'],
                    'similarity_score': match['similarity_score'],
                    'name_similarity': match['name_similarity'],
                    'club_similarity': match['club_similarity']
                })
            else:
                unmatched.append(draft_player)
        
        result = {
            'matches': matches,
            'unmatched': unmatched,
            'total_draft_players': len(draft_players),
            'matched_count': len(matches),
            'unmatched_count': len(unmatched),
            'match_rate': len(matches) / len(draft_players) * 100 if draft_players else 0,
            'last_updated': datetime.utcnow().isoformat()
        }
        
        # Save matching results
        self.save_data(result, 'player_matches')
        
        print(f"Matching complete: {len(matches)}/{len(draft_players)} players matched ({result['match_rate']:.1f}%)")
        
        return result
    
    def get_cache_status(self) -> Dict[str, Any]:
        """Get status of all cached data"""
        status = {}
        
        cache_types = ['tournaments', 'players', 'player_stats', 'player_matches']
        
        for cache_type in cache_types:
            data = self.load_data(cache_type)
            if data:
                status[cache_type] = {
                    'exists': True,
                    'last_updated': data.get('last_updated'),
                    'total_items': data.get(f'total_{cache_type.rstrip("s")}', 0)
                }
            else:
                status[cache_type] = {'exists': False}
        
        return status
    
    def clear_cache(self, cache_type: Optional[str] = None):
        """Clear cache data"""
        if cache_type:
            cache_types = [cache_type]
        else:
            cache_types = ['tournaments', 'players', 'player_stats', 'player_matches']
        
        for ct in cache_types:
            # Remove local cache
            cache_path = self.get_local_cache_path(ct)
            if os.path.exists(cache_path):
                os.remove(cache_path)
                print(f"Removed local cache: {cache_path}")
            
            # Remove S3 cache
            if self.s3_client and self.bucket_name:
                try:
                    s3_key = self.get_s3_key(ct)
                    self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
                    print(f"Removed S3 cache: s3://{self.bucket_name}/{s3_key}")
                except Exception as e:
                    print(f"Error removing S3 cache: {e}")


# Global instance
mantra_store = MantraDataStore()
