from flask import Blueprint, render_template, request, jsonify, session
from .auth import require_auth
from .mantra_store import MantraDataStore
from .mantra_api import PlayerMatcher
from .top4_services import load_state
import json

bp = Blueprint('player_mapping', __name__)

@bp.route('/top4/player-mapping')
@require_auth
def player_mapping_page():
    """Display page for reviewing and confirming player mappings"""
    if not session.get('godmode'):
        return "Access denied", 403
    
    return render_template('player_mapping.html')

@bp.route('/top4/player-mapping/preview')
@require_auth
def preview_mappings():
    """Generate and return preview of all player mappings"""
    if not session.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Load MantraFootball data from cache (S3 + local)
        mantra_store = MantraDataStore()
        
        # Check cache status first
        cache_status = mantra_store.get_cache_status()
        print(f"[PlayerMapping] Cache status: {cache_status}")
        
        mantra_players = mantra_store.get_players()
        print(f"[PlayerMapping] Loaded {len(mantra_players) if mantra_players else 0} players from cache")
        
        # If no players in cache, try to force load from S3
        if not mantra_players:
            print("[PlayerMapping] No players in cache, trying to force load from S3...")
            s3_data = mantra_store.load_from_s3('players')
            if s3_data:
                mantra_store.save_to_local_cache(s3_data, 'players')
                mantra_players = s3_data.get('players', [])
                print(f"[PlayerMapping] Force loaded {len(mantra_players)} players from S3")
        
        # Validate player data structure
        if mantra_players:
            print(f"[PlayerMapping] First player sample: {type(mantra_players[0])} = {str(mantra_players[0])[:200]}...")
            # Filter out any non-dict players
            valid_players = [p for p in mantra_players if isinstance(p, dict)]
            if len(valid_players) != len(mantra_players):
                print(f"[PlayerMapping] Filtered out {len(mantra_players) - len(valid_players)} invalid players")
                mantra_players = valid_players
        
        if not mantra_players:
            return jsonify({
                'error': 'No MantraFootball players found in cache or S3. Please sync players first using "👥 Синхронизировать игроков" button.',
                'cache_status': cache_status
            }), 400
        
        # Load draft state to get current players
        state = load_state()
        draft_players = []
        
        # Extract all draft players from state
        # Check both 'managers' and 'rosters' structures
        if 'managers' in state:
            for manager_name, manager_data in state.get('managers', {}).items():
                roster = manager_data.get('roster', []) if isinstance(manager_data, dict) else []
                for player in roster:
                    if isinstance(player, dict) and player not in draft_players:
                        draft_players.append(player)
        elif 'rosters' in state:
            for manager_name, roster in state.get('rosters', {}).items():
                for player in roster:
                    if isinstance(player, dict) and player not in draft_players:
                        draft_players.append(player)
        
        # Debug: show what draft players we have
        print(f"[PlayerMapping] Current draft_players count: {len(draft_players)}")
        if draft_players:
            print(f"[PlayerMapping] Sample draft players:")
            for i, player in enumerate(draft_players[:3]):
                print(f"  - {i}: {player}")
        
        # FORCE load TOP-4 players with Russian names (ignore existing draft_players)
        print(f"[PlayerMapping] Force loading TOP-4 players with Russian names...")
        
        # Always try to load Russian names, even if draft_players exist
        if True:
            try:
                import json
                import os
                
                # Try multiple possible paths for the TOP-4 players file
                possible_paths = [
                    os.path.join('data', 'cache', 'top4_players.json'),
                    'top4_players.json',
                    os.path.join('..', 'data', 'cache', 'top4_players.json'),
                    '/app/data/cache/top4_players.json'  # Heroku absolute path
                ]
                
                top4_players_file = None
                for path in possible_paths:
                    if os.path.exists(path):
                        top4_players_file = path
                        print(f"[PlayerMapping] Found TOP-4 players file at: {path}")
                        break
                    else:
                        print(f"[PlayerMapping] File not found: {path}")
                
                if top4_players_file:
                    try:
                        with open(top4_players_file, 'r', encoding='utf-8') as f:
                            content = f.read()
                            print(f"[PlayerMapping] File content preview (first 200 chars): {content[:200]}")
                            
                            # Check if it's actually JSON
                            if content.strip().startswith('<!DOCTYPE') or content.strip().startswith('<html'):
                                print(f"[PlayerMapping] ERROR: File contains HTML, not JSON!")
                                raise ValueError("File contains HTML instead of JSON")
                            
                            # Parse JSON
                            top4_data = json.loads(content)
                            
                            # REPLACE existing draft_players with Russian names
                            draft_players = [
                                {
                                    'name': player.get('fullName', ''),
                                    'club': player.get('clubName', ''),
                                    'position': player.get('position', ''),
                                    'league': player.get('league', ''),
                                    'playerId': player.get('playerId', '')
                                }
                                for player in top4_data if isinstance(player, dict)
                            ]
                            print(f"[PlayerMapping] REPLACED with {len(draft_players)} TOP-4 players with Russian names")
                            # Debug: show first few TOP-4 players
                            for i, player in enumerate(draft_players[:5]):
                                print(f"[PlayerMapping] Russian Player {i}: '{player.get('name')}' ({player.get('club')}) - {player.get('league')}")
                    
                    except json.JSONDecodeError as e:
                        print(f"[PlayerMapping] JSON decode error: {e}")
                        print(f"[PlayerMapping] File content (first 500 chars): {content[:500]}")
                        raise
                    except Exception as e:
                        print(f"[PlayerMapping] Error reading file: {e}")
                        raise
                else:
                    print(f"[PlayerMapping] TOP-4 players file not found in any of the paths: {possible_paths}")
                    # List current directory contents for debugging
                    print(f"[PlayerMapping] Current working directory: {os.getcwd()}")
                    print(f"[PlayerMapping] Directory contents: {os.listdir('.')}")
                    if os.path.exists('data'):
                        print(f"[PlayerMapping] data/ contents: {os.listdir('data')}")
                        if os.path.exists('data/cache'):
                            print(f"[PlayerMapping] data/cache/ contents: {os.listdir('data/cache')}")
                            
            except Exception as e:
                print(f"[PlayerMapping] Error loading TOP-4 players: {e}")
                import traceback
                traceback.print_exc()
        
        # If loading failed, use Russian test players for matching
        if len(draft_players) < 1000:  # If we don't have enough players, add Russian test names
            print(f"[PlayerMapping] Adding Russian test players for matching...")
            russian_test_players = [
                {'name': 'Мбаппе', 'club': 'Реал Мадрид', 'league': 'La Liga'},
                {'name': 'Холанд', 'club': 'Манчестер Сити', 'league': 'EPL'},
                {'name': 'Беллингем', 'club': 'Реал Мадрид', 'league': 'La Liga'},
                {'name': 'Винисиус Жуниор', 'club': 'Реал Мадрид', 'league': 'La Liga'},
                {'name': 'Де Брёйне', 'club': 'Манчестер Сити', 'league': 'EPL'},
                {'name': 'Левандовски', 'club': 'Барселона', 'league': 'La Liga'},
                {'name': 'Салах', 'club': 'Ливерпуль', 'league': 'EPL'},
                {'name': 'Кейн', 'club': 'Бавария', 'league': 'Bundesliga'},
                {'name': 'Модрич', 'club': 'Реал Мадрид', 'league': 'La Liga'},
                {'name': 'Бензема', 'club': 'Аль-Иттихад', 'league': 'Saudi'},
                {'name': 'Роналду', 'club': 'Аль-Насср', 'league': 'Saudi'},
                {'name': 'Месси', 'club': 'Интер Майами', 'league': 'MLS'},
            ]
            
            # Replace or extend draft_players with Russian names
            draft_players = russian_test_players
            print(f"[PlayerMapping] Using {len(draft_players)} Russian test players:")
            for i, player in enumerate(draft_players):
                print(f"[PlayerMapping] Russian Test Player {i}: '{player.get('name')}' ({player.get('club')}) - {player.get('league')}")
        
        # Generate mappings for all MantraFootball players
        mappings = []
        matched_count = 0
        
        for i, mantra_player in enumerate(mantra_players):
            # Debug: check if mantra_player is a dict
            if not isinstance(mantra_player, dict):
                print(f"[PlayerMapping] WARNING: Player {i} is not a dict: {type(mantra_player)} = {mantra_player}")
                continue
            
            # Safely extract player data
            # In MantraFootball data: 'name' contains last name, 'first_name' contains first name
            last_name = mantra_player.get('name', '') if isinstance(mantra_player.get('name'), str) else ''
            first_name = mantra_player.get('first_name', '') if isinstance(mantra_player.get('first_name'), str) else ''
            
            # Try different name combinations for better matching
            mantra_name_variations = []
            if first_name and last_name:
                mantra_name_variations.extend([
                    f"{first_name} {last_name}",  # "Emil Audero"
                    f"{last_name} {first_name}",  # "Audero Emil" 
                    f"{last_name}",               # "Audero"
                    f"{first_name}",              # "Emil"
                ])
            elif last_name:
                mantra_name_variations.append(last_name)
            elif first_name:
                mantra_name_variations.append(first_name)
            
            # Use the most complete name as primary
            mantra_name = mantra_name_variations[0] if mantra_name_variations else ''
            
            # Safely extract club data BEFORE debug logging
            club_data = mantra_player.get('club', {})
            if isinstance(club_data, dict):
                mantra_club = club_data.get('name', '')
            elif isinstance(club_data, str):
                mantra_club = club_data
            else:
                mantra_club = ''
            
            # Debug: log first few players to see name variations
            if i < 5:
                # print(f"[PlayerMapping] MantraFootball Player {i}: {mantra_name_variations} -> primary: '{mantra_name}', club: '{mantra_club}'")  # Commented out for performance
                pass
            
            # Try to find best match among draft players
            # We want to find which draft player best matches our current mantra player
            # Create a fake mantra player object for the search
            fake_mantra_player = {'name': mantra_name, 'club': mantra_club}
            
            # Find best matching draft player (search through draft_players)
            best_match = None
            best_score = 0.0
            
            for draft_player in draft_players:
                if not isinstance(draft_player, dict):
                    continue
                
                draft_name = draft_player.get('name', '')
                draft_club = draft_player.get('club', '')
                
                # Try all name variations to find the best match
                best_name_similarity = 0.0
                best_name_variation = ""
                for name_variation in mantra_name_variations:
                    name_sim = PlayerMatcher.calculate_name_similarity(name_variation, draft_name)
                    if name_sim > best_name_similarity:
                        best_name_similarity = name_sim
                        best_name_variation = name_variation
                
                # Calculate club similarity
                club_similarity = PlayerMatcher.calculate_club_similarity(mantra_club, draft_club)
                
                # Combined score (name is more important than club)
                combined_score = (best_name_similarity * 0.7) + (club_similarity * 0.3)
                
                # Debug: log detailed matching for first few players
                if i < 3 and combined_score > 0.1:  # Show any meaningful matches
                    # print(f"[PlayerMapping] Match attempt {i}: '{best_name_variation}' vs '{draft_name}' (name: {best_name_similarity:.3f}) | '{mantra_club}' vs '{draft_club}' (club: {club_similarity:.3f}) -> combined: {combined_score:.3f}")  # Commented out for performance
                    pass
                
                if combined_score > best_score and combined_score > 0.4:  # Minimum threshold
                    best_score = combined_score
                    best_match = {
                        'mantra_player': draft_player,  # This is actually the matched draft player
                        'similarity_score': combined_score,
                        'name_similarity': best_name_similarity,
                        'club_similarity': club_similarity
                    }
            
            # Safely extract additional data
            mantra_id = mantra_player.get('id', '')
            mantra_position = mantra_player.get('position', '')
            
            # Safely extract league info
            mantra_league = ''
            if isinstance(club_data, dict):
                tournament_data = club_data.get('tournament', {})
                if isinstance(tournament_data, dict):
                    mantra_league = tournament_data.get('name', '')
            
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
        
        return jsonify({
            'success': True,
            'mappings': mappings,
            'total_mantra_players': len(mantra_players),
            'total_draft_players': len(draft_players),
            'matched_count': matched_count,
            'match_rate': (matched_count / len(mantra_players)) * 100 if mantra_players else 0
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to generate mappings: {str(e)}'}), 500

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
        
        # Save mappings to the player mapping store
        mantra_store = MantraDataStore()
        confirmed_mappings = {}
        
        for mapping in mappings:
            if mapping.get('draft_match') and mapping.get('confirmed', False):
                mantra_id = mapping['mantra_id']
                draft_name = mapping['draft_match']['name']
                draft_club = mapping['draft_match']['club']
                
                confirmed_mappings[str(mantra_id)] = {
                    'draft_name': draft_name,
                    'draft_club': draft_club,
                    'similarity_score': mapping['similarity_score'],
                    'confirmed_at': data.get('timestamp'),
                    'confirmed_by': session.get('user_id')
                }
        
        # Save to S3 and local cache
        mantra_store.save_player_map(confirmed_mappings)
        
        return jsonify({
            'success': True,
            'confirmed_count': len(confirmed_mappings),
            'message': f'Successfully confirmed {len(confirmed_mappings)} player mappings'
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to save mappings: {str(e)}'}), 500

@bp.route('/top4/player-mapping/update', methods=['POST'])
@require_auth
def update_mapping():
    """Update a single player mapping"""
    if not session.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        mantra_id = data.get('mantra_id')
        draft_name = data.get('draft_name')
        draft_club = data.get('draft_club')
        
        if not all([mantra_id, draft_name]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Load existing mappings
        mantra_store = MantraDataStore()
        existing_mappings = mantra_store.load_player_map()
        
        # Update the specific mapping
        existing_mappings[str(mantra_id)] = {
            'draft_name': draft_name,
            'draft_club': draft_club or '',
            'similarity_score': 1.0,  # Manual mapping gets perfect score
            'manual_override': True,
            'updated_at': data.get('timestamp'),
            'updated_by': session.get('user_id')
        }
        
        # Save updated mappings
        mantra_store.save_player_map(existing_mappings)
        
        return jsonify({'success': True, 'message': 'Mapping updated successfully'})
        
    except Exception as e:
        return jsonify({'error': f'Failed to update mapping: {str(e)}'}), 500
