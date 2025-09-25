from flask import Blueprint, render_template, request, jsonify, session
from .auth import require_auth
from .mantra_store import MantraDataStore
from .mantra_api import PlayerMatcher
from .top4_services import load_state
import json
import threading
import time
import os
from datetime import datetime

bp = Blueprint('player_mapping', __name__)

# Global state for async mapping process
mapping_status = {
    'in_progress': False,
    'progress': 0,
    'total': 0,
    'current_step': '',
    'result': None,
    'error': None,
    'started_at': None,
    'completed_at': None
}

@bp.route('/top4/player-mapping')
@require_auth
def player_mapping_page():
    """Display page for reviewing and confirming player mappings"""
    if not session.get('godmode'):
        return "Access denied", 403
    
    return render_template('player_mapping.html')

def run_mapping_task():
    """Background task to generate player mappings"""
    global mapping_status
    
    try:
        mapping_status.update({
            'in_progress': True,
            'progress': 0,
            'total': 0,
            'current_step': 'Initializing...',
            'result': None,
            'error': None,
            'started_at': datetime.now().isoformat()
        })
        
        # Load MantraFootball data from cache (S3 + local)
        mapping_status['current_step'] = 'Loading MantraFootball data...'
        mantra_store = MantraDataStore()
        
        # Check cache status first
        cache_status = mantra_store.get_cache_status()
        print(f"[PlayerMapping] Cache status: {cache_status}")
        
        mantra_players = mantra_store.get_players()
        print(f"[PlayerMapping] Loaded {len(mantra_players) if mantra_players else 0} players from cache")
        
        if not mantra_players:
            print("[PlayerMapping] No players in cache, trying to force load from S3...")
            s3_data = mantra_store.load_from_s3('players')
            if s3_data:
                mantra_store.save_to_local_cache(s3_data, 'players')
                mantra_players = mantra_store.get_players()
                print(f"[PlayerMapping] Force loaded {len(mantra_players)} players from S3")
        
        if mantra_players and len(mantra_players) > 0:
            print(f"[PlayerMapping] First player sample: {type(mantra_players[0])} = {str(mantra_players[0])[:200]}...")
        
        # Filter out invalid players (must be dictionaries)
        if mantra_players:
            valid_players = [p for p in mantra_players if isinstance(p, dict)]
            if len(valid_players) != len(mantra_players):
                print(f"[PlayerMapping] Filtered out {len(mantra_players) - len(valid_players)} invalid players")
                mantra_players = valid_players
        
        if not mantra_players:
            raise Exception("No MantraFootball players found. Please sync players first.")
        
        mapping_status.update({
            'current_step': 'Loading draft players...',
            'progress': 5
        })
        
        # Add some hardcoded Russian test players for testing
        print(f"[PlayerMapping] Using Russian test players for matching...")
        
        russian_test_players = [
            {"name": "Мбаппе", "club": "Реал Мадрид", "league": "Spain"},
            {"name": "Холанд", "club": "Манчестер Сити", "league": "England"},
            {"name": "Беллингем", "club": "Реал Мадрид", "league": "Spain"},
            {"name": "Винисиус", "club": "Реал Мадрид", "league": "Spain"},
            {"name": "Родриго", "club": "Манчестер Сити", "league": "England"},
            {"name": "Де Брейне", "club": "Манчестер Сити", "league": "England"},
            {"name": "Салах", "club": "Ливерпуль", "league": "England"},
            {"name": "Левандовский", "club": "Барселона", "league": "Spain"},
            {"name": "Мюллер", "club": "Бавария", "league": "Germany"},
            {"name": "Нойер", "club": "Бавария", "league": "Germany"}
        ]
        
        draft_players = russian_test_players
        print(f"[PlayerMapping] Using {len(draft_players)} Russian test players")
        
        mapping_status.update({
            'current_step': 'Starting player matching...',
            'progress': 10,
            'total': len(mantra_players)
        })
        
        # Generate mappings with progress tracking
        mappings = []
        matched_count = 0
        matcher = PlayerMatcher()
        
        for i, mantra_player in enumerate(mantra_players):
            # Update progress every 50 players
            if i % 50 == 0:
                progress_percent = 10 + int((i / len(mantra_players)) * 80)  # 10-90%
                mapping_status.update({
                    'current_step': f'Matching player {i+1}/{len(mantra_players)}...',
                    'progress': progress_percent
                })
            
            # Validate mantra_player is a dictionary
            if not isinstance(mantra_player, dict):
                continue
            
            # Extract MantraFootball player info
            mantra_id = mantra_player.get('mantra_id') or mantra_player.get('id')
            
            # Handle name variations (last name, first name combinations)
            last_name = mantra_player.get('name', '')
            first_name = mantra_player.get('first_name', '')
            
            # Generate all possible name combinations
            mantra_name_variations = []
            if last_name and first_name:
                mantra_name_variations = [
                    f"{first_name} {last_name}",  # "Emil Audero"
                    f"{last_name} {first_name}",  # "Audero Emil"
                    last_name,                    # "Audero"
                    first_name                    # "Emil"
                ]
            elif last_name:
                mantra_name_variations = [last_name]
            elif first_name:
                mantra_name_variations = [first_name]
            
            # Primary name for display
            mantra_name = mantra_name_variations[0] if mantra_name_variations else 'Unknown'
            
            mantra_position = mantra_player.get('position', '')
            
            # Extract league info
            tournament_data = mantra_player.get('tournament', {})
            if isinstance(tournament_data, dict):
                mantra_league = tournament_data.get('name', '')
            else:
                mantra_league = str(tournament_data) if tournament_data else ''
            
            # Extract club info safely
            club_data = mantra_player.get('club', {})
            if isinstance(club_data, dict):
                mantra_club = club_data.get('name', '')
            elif isinstance(club_data, str):
                mantra_club = club_data
            else:
                mantra_club = ''
            
            # Find best match among draft players
            best_match = None
            best_score = 0
            
            # Calculate similarity with each draft player
            for draft_player in draft_players:
                if not isinstance(draft_player, dict):
                    continue
                
                draft_name = draft_player.get('name', '')
                draft_club = draft_player.get('club', '')
                
                # Calculate best name similarity across all variations
                best_name_similarity = 0
                for name_variation in mantra_name_variations:
                    name_similarity = matcher.calculate_name_similarity(name_variation, draft_name)
                    if name_similarity > best_name_similarity:
                        best_name_similarity = name_similarity
                
                # Calculate club similarity
                club_similarity = PlayerMatcher.calculate_club_similarity(mantra_club, draft_club)
                
                # Combined score (name is more important than club)
                combined_score = (best_name_similarity * 0.7) + (club_similarity * 0.3)
                
                if combined_score > best_score and combined_score > 0.4:  # Minimum threshold
                    best_score = combined_score
                    best_match = {
                        'mantra_player': draft_player,  # This is actually the matched draft player
                        'similarity_score': combined_score,
                        'name_similarity': best_name_similarity,
                        'club_similarity': club_similarity
                    }
            
            # Create mapping entry
            mapping_entry = {
                'mantra_id': mantra_id,
                'mantra_name': mantra_name,
                'mantra_club': mantra_club,
                'mantra_position': mantra_position,
                'mantra_league': mantra_league,
                'draft_match': None,
                'similarity_score': 0.0,
                'auto_matched': False
            }
            
            if best_match:
                matched_count += 1
                draft_player = best_match['mantra_player']  # This is actually the matched draft player
                mapping_entry.update({
                    'draft_match': {
                        'name': draft_player.get('name', ''),
                        'club': draft_player.get('club', '')
                    },
                    'similarity_score': best_match['similarity_score'],
                    'auto_matched': True
                })
            
            mappings.append(mapping_entry)
        
        # Sort by similarity score (best matches first)
        mappings.sort(key=lambda x: x['similarity_score'], reverse=True)
        
        # Final result
        result = {
            'success': True,
            'mappings': mappings,
            'total_mantra_players': len(mantra_players),
            'total_draft_players': len(draft_players),
            'matched_count': matched_count,
            'match_rate': (matched_count / len(mantra_players)) * 100 if mantra_players else 0,
            'cache_status': cache_status
        }
        
        mapping_status.update({
            'in_progress': False,
            'progress': 100,
            'current_step': 'Completed',
            'result': result,
            'completed_at': datetime.now().isoformat()
        })
        
        print(f"[PlayerMapping] Async mapping completed: {matched_count}/{len(mantra_players)} matches")
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[PlayerMapping] ERROR in run_mapping_task: {str(e)}")
        print(f"[PlayerMapping] Full traceback: {error_details}")
        
        mapping_status.update({
            'in_progress': False,
            'error': f'Failed to generate mappings: {str(e)}',
            'completed_at': datetime.now().isoformat()
        })

@bp.route('/top4/player-mapping/preview')
@require_auth
def preview_mappings():
    """Start async mapping process or return current status"""
    global mapping_status
    
    try:
        if not session.get('godmode'):
            return jsonify({'error': 'Access denied'}), 403
        
        # If already in progress, return status
        if mapping_status['in_progress']:
            return jsonify({
                'success': False,
                'in_progress': True,
                'progress': mapping_status['progress'],
                'total': mapping_status['total'],
                'current_step': mapping_status['current_step'],
                'started_at': mapping_status['started_at']
            })
        
        # If completed, return cached result
        if mapping_status['result']:
            return jsonify(mapping_status['result'])
        
        # If error, return error
        if mapping_status['error']:
            return jsonify({'error': mapping_status['error']}), 500
        
        # Start new mapping task
        mapping_status = {
            'in_progress': True,
            'progress': 0,
            'total': 0,
            'current_step': 'Starting...',
            'result': None,
            'error': None,
            'started_at': datetime.now().isoformat(),
            'completed_at': None
        }
        
        # Start background thread
        thread = threading.Thread(target=run_mapping_task)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': False,
            'in_progress': True,
            'progress': 0,
            'current_step': 'Starting background mapping...',
            'started_at': mapping_status['started_at']
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[PlayerMapping] ERROR in preview_mappings: {str(e)}")
        print(f"[PlayerMapping] Full traceback: {error_details}")
        return jsonify({
            'error': f'Failed to start mapping: {str(e)}',
            'details': error_details
        }), 500

@bp.route('/top4/player-mapping/status')
@require_auth
def mapping_status_endpoint():
    """Get current status of mapping process"""
    global mapping_status
    
    if not session.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    return jsonify({
        'in_progress': mapping_status['in_progress'],
        'progress': mapping_status['progress'],
        'total': mapping_status['total'],
        'current_step': mapping_status['current_step'],
        'started_at': mapping_status['started_at'],
        'completed_at': mapping_status['completed_at'],
        'has_result': mapping_status['result'] is not None,
        'has_error': mapping_status['error'] is not None,
        'error': mapping_status['error']
    })

@bp.route('/top4/player-mapping/confirm', methods=['POST'])
@require_auth
def confirm_mappings():
    """Confirm and save player mappings to the mapping store"""
    if not session.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        mappings = data.get('mappings', [])
        
        if not mappings:
            return jsonify({'error': 'No mappings provided'}), 400
        
        # Save mappings to the mapping store
        mantra_store = MantraDataStore()
        player_map = {}
        
        for mapping in mappings:
            if mapping.get('draft_match') and mapping.get('mantra_id'):
                player_map[str(mapping['mantra_id'])] = {
                    'draft_name': mapping['draft_match']['name'],
                    'draft_club': mapping['draft_match']['club'],
                    'similarity_score': mapping.get('similarity_score', 0),
                    'confirmed_at': datetime.now().isoformat()
                }
        
        mantra_store.save_player_map(player_map)
        
        return jsonify({
            'success': True,
            'message': f'Saved {len(player_map)} player mappings',
            'saved_count': len(player_map)
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[PlayerMapping] ERROR in confirm_mappings: {str(e)}")
        print(f"[PlayerMapping] Full traceback: {error_details}")
        return jsonify({
            'error': f'Failed to save mappings: {str(e)}',
            'details': error_details
        }), 500

@bp.route('/top4/player-mapping/update', methods=['POST'])
@require_auth
def update_mapping():
    """Update a single mapping"""
    if not session.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        mantra_id = data.get('mantra_id')
        draft_match = data.get('draft_match')
        
        if not mantra_id:
            return jsonify({'error': 'mantra_id is required'}), 400
        
        # Load existing mappings
        mantra_store = MantraDataStore()
        player_map = mantra_store.load_player_map() or {}
        
        if draft_match:
            # Update mapping
            player_map[str(mantra_id)] = {
                'draft_name': draft_match['name'],
                'draft_club': draft_match['club'],
                'similarity_score': data.get('similarity_score', 0),
                'updated_at': datetime.now().isoformat()
            }
        else:
            # Remove mapping
            player_map.pop(str(mantra_id), None)
        
        # Save updated mappings
        mantra_store.save_player_map(player_map)
        
        return jsonify({
            'success': True,
            'message': 'Mapping updated successfully'
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[PlayerMapping] ERROR in update_mapping: {str(e)}")
        print(f"[PlayerMapping] Full traceback: {error_details}")
        return jsonify({
            'error': f'Failed to update mapping: {str(e)}',
            'details': error_details
        }), 500
