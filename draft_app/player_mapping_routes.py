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
        
        # Load real TOP-4 players with Russian names
        mapping_status['current_step'] = 'Loading TOP-4 players...'
        mapping_status['progress'] = 7
        
        draft_players = []
        
        # Try to load from the actual file - prioritize the updated file
        possible_paths = [
            'data/cache/top4_players.json',  # Primary path for production
            './data/cache/top4_players.json',  # Relative path
            '/app/data/cache/top4_players.json',  # Heroku path
            '/Users/ruslan.aharodnik/Code/perpay/external/draft-app/data/cache/top4_players.json'  # Development path
        ]
        
        top4_loaded = False
        
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        top4_data = json.load(f)
                        if isinstance(top4_data, list) and len(top4_data) > 0:
                            # Convert to our format
                            draft_players = []
                            for player in top4_data:
                                if isinstance(player, dict):
                                    draft_players.append({
                                        'name': player.get('fullName', ''),
                                        'club': player.get('clubName', ''),
                                        'position': player.get('position', ''),
                                        'league': player.get('league', ''),
                                        'playerId': player.get('playerId', '')
                                    })
                            
                            print(f"[PlayerMapping] Loaded {len(draft_players)} TOP-4 players from {path}")
                            # Show some sample players for verification
                            if len(draft_players) > 0:
                                sample_players = draft_players[:3]
                                print(f"[PlayerMapping] Sample players: {[p.get('name') + ' (' + p.get('club') + ')' for p in sample_players]}")
                                # Check file modification time to ensure we're using the updated file
                                file_stat = os.stat(path)
                                mod_time = file_stat.st_mtime
                                print(f"[PlayerMapping] File last modified: {mod_time} (using updated file: {mod_time > 1727200000})")  # Rough timestamp check
                            top4_loaded = True
                            break
                except Exception as e:
                    print(f"[PlayerMapping] Error loading {path}: {e}")
                    continue
        
        # Fallback to test players if file not found
        if not top4_loaded or len(draft_players) == 0:
            print(f"[PlayerMapping] Using Russian test players for matching...")
            
            draft_players = [
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
            print(f"[PlayerMapping] Using {len(draft_players)} fallback test players")
        else:
            print(f"[PlayerMapping] Successfully loaded {len(draft_players)} real TOP-4 players")
        
        mapping_status.update({
            'current_step': 'Starting player matching...',
            'progress': 10,
            'total': len(mantra_players)
        })
        
        # Group players by club for more efficient matching
        mapping_status['current_step'] = 'Organizing players by clubs...'
        mapping_status['progress'] = 12
        
        # Group MantraFootball players by club
        mantra_players_by_club = {}
        for player in mantra_players:
            if not isinstance(player, dict):
                continue
            
            club_data = player.get('club', {})
            if isinstance(club_data, dict):
                club_name = club_data.get('name', 'Unknown')
            elif isinstance(club_data, str):
                club_name = club_data
            else:
                club_name = 'Unknown'
            
            if club_name not in mantra_players_by_club:
                mantra_players_by_club[club_name] = []
            mantra_players_by_club[club_name].append(player)
        
        # Group draft players by club
        draft_players_by_club = {}
        for player in draft_players:
            club_name = player.get('club', 'Unknown')  # 'club' is correct here from our conversion above
            if club_name not in draft_players_by_club:
                draft_players_by_club[club_name] = []
            draft_players_by_club[club_name].append(player)
        
        print(f"[PlayerMapping] MantraFootball clubs: {len(mantra_players_by_club)}")
        print(f"[PlayerMapping] Draft clubs: {len(draft_players_by_club)}")
        
        # Generate mappings by processing each club
        mappings = []
        matched_count = 0
        matcher = PlayerMatcher()
        
        # Get all unique club names from both sources
        all_mantra_clubs = set(mantra_players_by_club.keys())
        all_draft_clubs = set(draft_players_by_club.keys())
        
        processed_players = 0
        total_mantra_players = sum(len(players) for players in mantra_players_by_club.values())
        
        print(f"[PlayerMapping] Starting club-by-club matching for {len(all_mantra_clubs)} MantraFootball clubs")
        
        # Load existing player mappings from S3 to exclude already mapped pairs
        mapping_status['current_step'] = 'Loading existing mappings from S3...'
        mantra_store = MantraDataStore()
        existing_player_map = mantra_store.load_player_map() or {}
        
        print(f"[PlayerMapping] Loaded {len(existing_player_map)} existing mappings from S3")
        
        # Track used draft players to ensure 1-to-1 mapping
        used_draft_players = set()
        
        # Pre-populate used_draft_players with existing mappings
        for mantra_id, mapping_data in existing_player_map.items():
            if isinstance(mapping_data, dict):
                draft_name = mapping_data.get('draft_name')
                draft_club = mapping_data.get('draft_club')
                if draft_name and draft_club and draft_name != '__UNMAPPED__':
                    draft_key = f"{draft_name}|{draft_club}"
                    used_draft_players.add(draft_key)
                    print(f"[PlayerMapping] Pre-excluding already mapped pair: MantraID {mantra_id} -> {draft_name} ({draft_club})")
        
        print(f"[PlayerMapping] Pre-excluded {len(used_draft_players)} already mapped draft players")
        
        # Process each MantraFootball club
        for club_idx, (mantra_club, mantra_club_players) in enumerate(mantra_players_by_club.items()):
            # Update progress
            progress_percent = 15 + int((club_idx / len(mantra_players_by_club)) * 75)  # 15-90%
            mapping_status.update({
                'current_step': f'Processing club {club_idx+1}/{len(mantra_players_by_club)}: {mantra_club} ({len(mantra_club_players)} players)...',
                'progress': progress_percent
            })
            
            # Find the best matching draft club
            best_club_match = None
            best_club_similarity = 0
            
            for draft_club in all_draft_clubs:
                club_similarity = PlayerMatcher.calculate_club_similarity(mantra_club, draft_club)
                if club_similarity > best_club_similarity:
                    best_club_similarity = club_similarity
                    best_club_match = draft_club
            
            # Get draft players from the best matching club (strict matching)
            if best_club_match and best_club_similarity > 0.6:  # Stricter threshold
                candidate_draft_players = draft_players_by_club[best_club_match]
                print(f"[PlayerMapping] Matching {mantra_club} -> {best_club_match} (similarity: {best_club_similarity:.3f}, {len(candidate_draft_players)} players)")
            elif best_club_match and best_club_similarity > 0.4:
                # Medium similarity - still use club-specific matching but note it
                candidate_draft_players = draft_players_by_club[best_club_match]
                print(f"[PlayerMapping] Weak club match: {mantra_club} -> {best_club_match} (similarity: {best_club_similarity:.3f}, {len(candidate_draft_players)} players)")
            else:
                # Skip this club entirely if no reasonable match - this prevents bad mappings
                print(f"[PlayerMapping] Skipping {mantra_club} - no reasonable club match found (best: {best_club_similarity:.3f})")
                processed_players += len(mantra_club_players)
                continue
            
            # IMPROVED: Two-pass matching for 1-to-1 mapping within each club
            # First pass: collect all potential matches for players in this club
            club_potential_matches = []
            
            for mantra_player in mantra_club_players:
                processed_players += 1
                
                # Validate mantra_player is a dictionary
                if not isinstance(mantra_player, dict):
                    continue
                
                # Extract MantraFootball player info
                mantra_id = mantra_player.get('mantra_id') or mantra_player.get('id')
                
                # Skip if this MantraFootball player is already mapped
                if str(mantra_id) in existing_player_map:
                    existing_mapping = existing_player_map[str(mantra_id)]
                    if isinstance(existing_mapping, dict):
                        draft_name = existing_mapping.get('draft_name')
                        if draft_name and draft_name != '__UNMAPPED__':
                            print(f"[PlayerMapping] Skipping already mapped MantraFootball player: {mantra_id} -> {draft_name}")
                            continue
                
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
                
                # Use the club we're currently processing
                mantra_club_name = mantra_club
                
                # Find best match among candidate draft players from this club
                best_match_for_this_player = None
                best_score_for_this_player = 0
                
                # Calculate similarity with candidate draft players
                # STRICT: Only match players from the SAME club
                for draft_player in candidate_draft_players:
                    if not isinstance(draft_player, dict):
                        continue
                    
                    draft_name = draft_player.get('name', '')
                    draft_club = draft_player.get('club', '')
                    
                    # Create unique key for this draft player
                    draft_key = f"{draft_name}|{draft_club}"
                    
                    # Skip if this draft player is already used
                    if draft_key in used_draft_players:
                        continue
                    
                    # STRICT CLUB MATCHING: Only consider players from the same club
                    club_similarity = PlayerMatcher.calculate_club_similarity(mantra_club_name, draft_club)
                    
                    # Only proceed if clubs match well (strict threshold)
                    if club_similarity < 0.4:
                        continue  # Skip players from very different clubs
                    
                    # Calculate best name similarity across all variations
                    best_name_similarity = 0
                    for name_variation in mantra_name_variations:
                        name_similarity = matcher.calculate_name_similarity(name_variation, draft_name)
                        if name_similarity > best_name_similarity:
                            best_name_similarity = name_similarity
                    
                    # For same-club matching, name similarity is the primary factor
                    # But adjust threshold based on club similarity
                    if club_similarity >= 0.7:
                        name_threshold = 0.3  # Lower threshold for well-matched clubs
                    else:
                        name_threshold = 0.6  # Higher threshold for weakly-matched clubs
                    
                    combined_score = best_name_similarity  # Club already verified above
                    
                    if combined_score > best_score_for_this_player and combined_score > name_threshold:
                        best_score_for_this_player = combined_score
                        best_match_for_this_player = {
                            'draft_player': draft_player,
                            'draft_key': draft_key,
                            'similarity_score': combined_score,
                            'name_similarity': best_name_similarity,
                            'club_similarity': club_similarity
                        }
                
                # Store potential match for later resolution
                club_potential_matches.append({
                    'mantra_player': mantra_player,
                    'mantra_id': mantra_id,
                    'mantra_name': mantra_name,
                    'mantra_club_name': mantra_club_name,
                    'mantra_position': mantra_position,
                    'mantra_league': mantra_league,
                    'best_match': best_match_for_this_player,
                    'best_score': best_score_for_this_player
                })
            
            # Second pass: resolve conflicts and assign matches (best scores first)
            club_potential_matches.sort(key=lambda x: x['best_score'], reverse=True)
            
            for potential_match in club_potential_matches:
                mantra_id = potential_match['mantra_id']
                mantra_name = potential_match['mantra_name']
                mantra_club_name = potential_match['mantra_club_name']
                mantra_position = potential_match['mantra_position']
                mantra_league = potential_match['mantra_league']
                best_match = potential_match['best_match']
                
                # Create mapping entry
                mapping_entry = {
                    'mantra_id': mantra_id,
                    'mantra_name': mantra_name,
                    'mantra_club': mantra_club_name,
                    'mantra_position': mantra_position,
                    'mantra_league': mantra_league,
                    'draft_match': None,
                    'similarity_score': 0.0,
                    'auto_matched': False
                }
                
                # Check if we can assign this match (draft player not already used)
                if best_match and best_match['draft_key'] not in used_draft_players:
                    # Assign this match
                    used_draft_players.add(best_match['draft_key'])
                    matched_count += 1
                    
                    draft_player = best_match['draft_player']
                    mapping_entry.update({
                        'draft_match': {
                            'name': draft_player.get('name', ''),
                            'club': draft_player.get('club', '')
                        },
                        'similarity_score': best_match['similarity_score'],
                        'auto_matched': True
                    })
                    
                    print(f"[PlayerMapping] ✅ Matched: {mantra_name} -> {draft_player.get('name', '')} (score: {best_match['similarity_score']:.3f})")
                else:
                    # No automatic match - find all remaining players from same club for manual selection
                    remaining_club_players = []
                    for draft_player in candidate_draft_players:
                        if not isinstance(draft_player, dict):
                            continue
                        
                        draft_name = draft_player.get('name', '')
                        draft_club = draft_player.get('club', '')
                        draft_key = f"{draft_name}|{draft_club}"
                        
                        # Only include players from same club that are not yet used
                        if draft_key not in used_draft_players:
                            club_similarity = PlayerMatcher.calculate_club_similarity(mantra_club_name, draft_club)
                            if club_similarity >= 0.4:  # Same threshold as before
                                # Calculate name similarity for sorting
                                best_name_similarity = 0
                                for name_variation in mantra_name_variations:
                                    name_similarity = matcher.calculate_name_similarity(name_variation, draft_name)
                                    if name_similarity > best_name_similarity:
                                        best_name_similarity = name_similarity
                                
                                remaining_club_players.append({
                                    'name': draft_name,
                                    'club': draft_club,
                                    'similarity_score': best_name_similarity
                                })
                    
                    # Sort remaining players by similarity (best first)
                    remaining_club_players.sort(key=lambda x: x['similarity_score'], reverse=True)
                    
                    # Add to mapping with suggestions for manual review
                    mapping_entry.update({
                        'remaining_club_options': remaining_club_players[:5],  # Top 5 suggestions
                        'total_remaining_options': len(remaining_club_players)
                    })
                    
                    if remaining_club_players:
                        print(f"[PlayerMapping] ⚠️ No auto-match for {mantra_name}, but {len(remaining_club_players)} options available from {mantra_club_name}")
                    else:
                        print(f"[PlayerMapping] ❌ No match: {mantra_name} (no remaining players in {mantra_club_name})")
                
                mappings.append(mapping_entry)
        
        # Add existing mappings to the result (so user can see what's already mapped)
        mapping_status['current_step'] = 'Adding existing mappings to results...'
        for mantra_id, existing_mapping in existing_player_map.items():
            if isinstance(existing_mapping, dict):
                draft_name = existing_mapping.get('draft_name')
                draft_club = existing_mapping.get('draft_club')
                
                # Skip if this is an unmapped entry
                if draft_name == '__UNMAPPED__':
                    continue
                
                # Find the corresponding MantraFootball player data
                mantra_player_data = None
                for players_list in mantra_players_by_club.values():
                    for player in players_list:
                        if isinstance(player, dict):
                            player_id = player.get('mantra_id') or player.get('id')
                            if str(player_id) == str(mantra_id):
                                mantra_player_data = player
                                break
                    if mantra_player_data:
                        break
                
                if mantra_player_data:
                    # Extract MantraFootball player info
                    last_name = mantra_player_data.get('name', '')
                    first_name = mantra_player_data.get('first_name', '')
                    mantra_name = f"{first_name} {last_name}".strip() if first_name and last_name else (last_name or first_name or 'Unknown')
                    
                    mantra_position = mantra_player_data.get('position', '')
                    
                    # Extract league info
                    tournament_data = mantra_player_data.get('tournament', {})
                    if isinstance(tournament_data, dict):
                        mantra_league = tournament_data.get('name', '')
                    else:
                        mantra_league = str(tournament_data) if tournament_data else ''
                    
                    # Extract club info
                    club_data = mantra_player_data.get('club', {})
                    if isinstance(club_data, dict):
                        mantra_club_name = club_data.get('name', '')
                    else:
                        mantra_club_name = str(club_data) if club_data else ''
                    
                    # Add existing mapping to results
                    existing_mapping_entry = {
                        'mantra_id': int(mantra_id),
                        'mantra_name': mantra_name,
                        'mantra_club': mantra_club_name,
                        'mantra_position': mantra_position,
                        'mantra_league': mantra_league,
                        'draft_match': {
                            'name': draft_name,
                            'club': draft_club
                        },
                        'similarity_score': existing_mapping.get('similarity_score', 1.0),
                        'auto_matched': True,
                        'is_saved_to_s3': True,
                        'saved_at': existing_mapping.get('saved_at', ''),
                        'is_high_confidence': existing_mapping.get('is_high_confidence', False)
                    }
                    
                    mappings.append(existing_mapping_entry)
        
        # Sort by similarity score (best matches first)
        mappings.sort(key=lambda x: x['similarity_score'], reverse=True)
        
        # Final result
        result = {
            'success': True,
            'mappings': mappings,
            'total_mantra_players': processed_players,
            'total_draft_players': len(draft_players),
            'matched_count': matched_count,
            'match_rate': (matched_count / processed_players) * 100 if processed_players > 0 else 0,
            'cache_status': cache_status,
            'note': f'Processed {processed_players} players from {len(mantra_players_by_club)} clubs using club-by-club matching',
            'clubs_processed': len(mantra_players_by_club)
        }
        
        mapping_status.update({
            'in_progress': False,
            'progress': 100,
            'current_step': 'Completed',
            'result': result,
            'completed_at': datetime.now().isoformat()
        })
        
        print(f"[PlayerMapping] Club-by-club mapping completed: {matched_count}/{processed_players} matches from {len(mantra_players_by_club)} clubs")
        
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

@bp.route('/top4/player-mapping/reset', methods=['POST'])
@require_auth
def reset_mapping():
    """Reset mapping status to allow new mapping process"""
    global mapping_status
    
    if not session.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
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
    
    return jsonify({
        'success': True,
        'message': 'Mapping status reset successfully'
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
        draft_name = data.get('draft_name', '').strip()
        draft_club = data.get('draft_club', '').strip()
        is_unmapped = data.get('is_unmapped', False)
        
        if not mantra_id:
            return jsonify({'error': 'mantra_id is required'}), 400
        
        # Load existing mappings
        mantra_store = MantraDataStore()
        player_map = mantra_store.load_player_map() or {}
        
        if is_unmapped:
            # Mark as unmapped (exclude from draft)
            player_map[str(mantra_id)] = {
                'draft_name': '__UNMAPPED__',
                'draft_club': '__UNMAPPED__',
                'is_unmapped': True,
                'similarity_score': 0,
                'updated_at': datetime.now().isoformat()
            }
        elif draft_name and draft_club:
            # Update mapping
            player_map[str(mantra_id)] = {
                'draft_name': draft_name,
                'draft_club': draft_club,
                'is_unmapped': False,
                'similarity_score': data.get('similarity_score', 1.0),
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

@bp.route('/top4/player-mapping/all-draft-players')
@require_auth
def get_all_draft_players():
    """Get all available draft players for dropdown"""
    try:
        # Check if user has godmode
        if not session.get('godmode'):
            return jsonify({'error': 'Access denied'}), 403
        
        # Load draft players from the same source as mapping
        draft_players = []
        
        # Try to load from the actual file
        possible_paths = [
            'data/cache/top4_players.json',  # Primary path for production
            './data/cache/top4_players.json',  # Relative path
            '/app/data/cache/top4_players.json',  # Heroku path
            '/Users/ruslan.aharodnik/Code/perpay/external/draft-app/data/cache/top4_players.json'  # Development path
        ]
        
        top4_loaded = False
        
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        top4_data = json.load(f)
                        if isinstance(top4_data, list) and len(top4_data) > 0:
                            # Convert to our format
                            draft_players = []
                            for player in top4_data:
                                if isinstance(player, dict):
                                    draft_players.append({
                                        'name': player.get('fullName', ''),
                                        'club': player.get('clubName', ''),
                                        'position': player.get('position', ''),
                                        'league': player.get('league', ''),
                                        'playerId': player.get('playerId', '')
                                    })
                            
                            print(f"[PlayerMapping] Loaded {len(draft_players)} draft players from {path}")
                            top4_loaded = True
                            break
                except Exception as e:
                    print(f"[PlayerMapping] Error loading {path}: {e}")
                    continue
        
        if not top4_loaded:
            return jsonify({'success': False, 'error': 'Could not load draft players'})
        
        return jsonify({
            'success': True, 
            'players': draft_players,
            'total_players': len(draft_players)
        })
        
    except Exception as e:
        print(f"[PlayerMapping] Error in get_all_draft_players: {e}")
        return jsonify({'success': False, 'error': str(e)})

@bp.route('/top4/player-mapping/auto-map-high-confidence', methods=['POST'])
@require_auth
def auto_map_high_confidence():
    """Automatically save high-confidence mappings (≥99.99%) to AWS S3"""
    try:
        if not session.get('godmode'):
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json()
        mappings = data.get('mappings', [])
        
        if not mappings:
            return jsonify({'error': 'No mappings provided'}), 400
        
        # Filter to only include high-confidence mappings
        high_confidence_mappings = [
            m for m in mappings 
            if m.get('similarity_score', 0) >= 0.9999 and m.get('auto_matched', False)
        ]
        
        if not high_confidence_mappings:
            return jsonify({'error': 'No high-confidence mappings found (≥99.99%)'}), 400
        
        # Load existing player mappings from S3
        mantra_store = MantraDataStore()
        existing_mappings = mantra_store.load_player_map() or {}
        
        saved_count = 0
        
        # Add high-confidence mappings to the player map
        for mapping in high_confidence_mappings:
            mantra_id = mapping.get('mantra_id')
            draft_match = mapping.get('draft_match')
            
            if mantra_id and draft_match and draft_match.get('name') and draft_match.get('club'):
                existing_mappings[str(mantra_id)] = {
                    'draft_name': draft_match['name'],
                    'draft_club': draft_match['club'],
                    'similarity_score': mapping.get('similarity_score', 1.0),
                    'is_high_confidence': True,
                    'auto_saved': True,
                    'saved_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }
                saved_count += 1
        
        # Save updated mappings back to S3
        if saved_count > 0:
            mantra_store.save_player_map(existing_mappings)
            print(f"[PlayerMapping] Auto-saved {saved_count} high-confidence mappings to S3")
        
        return jsonify({
            'success': True,
            'saved_count': saved_count,
            'message': f'Successfully saved {saved_count} high-confidence mappings to AWS S3'
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[PlayerMapping] ERROR in auto_map_high_confidence: {str(e)}")
        print(f"[PlayerMapping] Full traceback: {error_details}")
        return jsonify({
            'error': f'Failed to save high-confidence mappings: {str(e)}',
            'details': error_details
        }), 500
