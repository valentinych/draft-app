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
        # Load MantraFootball data
        mantra_store = MantraDataStore()
        mantra_players = mantra_store.get_players()
        
        if not mantra_players:
            return jsonify({
                'error': 'No MantraFootball players found. Please sync players first using "👥 Синхронизировать игроков" button.'
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
        
        # For demonstration purposes, create some sample draft players if none exist
        if not draft_players:
            draft_players = [
                {'name': 'Kylian Mbappe', 'club': 'Real Madrid'},
                {'name': 'Erling Haaland', 'club': 'Manchester City'},
                {'name': 'Jude Bellingham', 'club': 'Real Madrid'},
                {'name': 'Vinicius Junior', 'club': 'Real Madrid'},
                {'name': 'Kevin De Bruyne', 'club': 'Manchester City'},
                # Add more sample players as needed
            ]
        
        # Generate mappings for all MantraFootball players
        mappings = []
        matched_count = 0
        
        for mantra_player in mantra_players:
            mantra_name = f"{mantra_player.get('first_name', '')} {mantra_player.get('last_name', '')}".strip()
            mantra_club = mantra_player.get('club', {}).get('name', '') if isinstance(mantra_player.get('club'), dict) else mantra_player.get('club', '')
            
            # Try to find best match among draft players
            # Create a fake draft player object for matching
            fake_draft_player = {'name': mantra_name, 'club': mantra_club}
            best_match = PlayerMatcher.find_best_match(fake_draft_player, draft_players)
            
            mapping_entry = {
                'mantra_id': mantra_player.get('id'),
                'mantra_name': mantra_name,
                'mantra_club': mantra_club,
                'mantra_position': mantra_player.get('position', ''),
                'mantra_league': mantra_player.get('club', {}).get('tournament', {}).get('name', '') if isinstance(mantra_player.get('club'), dict) else '',
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
