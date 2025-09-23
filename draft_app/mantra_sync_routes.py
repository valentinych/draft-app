"""
MantraFootball synchronization routes
"""
from flask import Blueprint, request, jsonify, redirect, url_for
from .auth import require_auth
from .mantra_store import mantra_store
from .state import load_state
import threading
import time

bp = Blueprint('mantra_sync', __name__, url_prefix='/mantra')

# Global sync status
sync_status = {
    'tournaments': {'running': False, 'progress': 0, 'message': ''},
    'players': {'running': False, 'progress': 0, 'message': ''},
    'matching': {'running': False, 'progress': 0, 'message': ''}
}


def run_tournaments_sync():
    """Run tournaments sync in background"""
    global sync_status
    
    try:
        sync_status['tournaments']['running'] = True
        sync_status['tournaments']['progress'] = 0
        sync_status['tournaments']['message'] = 'Starting tournaments sync...'
        
        result = mantra_store.sync_tournaments()
        
        sync_status['tournaments']['progress'] = 100
        sync_status['tournaments']['message'] = f"Synced {result['total_tournaments']} tournaments"
        
    except Exception as e:
        sync_status['tournaments']['message'] = f"Error: {str(e)}"
    finally:
        sync_status['tournaments']['running'] = False


def run_players_sync(include_stats=False):
    """Run players sync in background"""
    global sync_status
    
    try:
        sync_status['players']['running'] = True
        sync_status['players']['progress'] = 0
        sync_status['players']['message'] = 'Starting players sync...'
        
        result = mantra_store.sync_players(include_stats=include_stats)
        
        sync_status['players']['progress'] = 100
        sync_status['players']['message'] = f"Synced {result['total_players']} players"
        
    except Exception as e:
        sync_status['players']['message'] = f"Error: {str(e)}"
    finally:
        sync_status['players']['running'] = False


def run_player_matching():
    """Run player matching in background"""
    global sync_status
    
    try:
        sync_status['matching']['running'] = True
        sync_status['matching']['progress'] = 0
        sync_status['matching']['message'] = 'Starting player matching...'
        
        # Load current TOP-4 draft players
        state = load_state('top4')
        draft_players = []
        
        # Extract all players from rosters
        for manager, roster in state.get('rosters', {}).items():
            for player in roster:
                if player not in draft_players:
                    draft_players.append(player)
        
        # Also get available players
        for player in state.get('players', []):
            if player not in draft_players:
                draft_players.append(player)
        
        sync_status['matching']['progress'] = 50
        sync_status['matching']['message'] = f'Matching {len(draft_players)} draft players...'
        
        result = mantra_store.match_draft_players_with_mantra(draft_players)
        
        sync_status['matching']['progress'] = 100
        sync_status['matching']['message'] = f"Matched {result['matched_count']}/{result['total_draft_players']} players ({result['match_rate']:.1f}%)"
        
    except Exception as e:
        sync_status['matching']['message'] = f"Error: {str(e)}"
    finally:
        sync_status['matching']['running'] = False


@bp.route('/sync/tournaments', methods=['POST'])
@require_auth
def sync_tournaments():
    """Sync tournaments data"""
    user = request.user
    if not user.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    if sync_status['tournaments']['running']:
        return jsonify({'error': 'Tournaments sync already running'}), 400
    
    # Start sync in background
    thread = threading.Thread(target=run_tournaments_sync)
    thread.daemon = True
    thread.start()
    
    return jsonify({'message': 'Tournaments sync started', 'status': 'running'})


@bp.route('/sync/players', methods=['POST'])
@require_auth
def sync_players():
    """Sync players data"""
    user = request.user
    if not user.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    if sync_status['players']['running']:
        return jsonify({'error': 'Players sync already running'}), 400
    
    include_stats = request.json.get('include_stats', False) if request.json else False
    
    # Start sync in background
    thread = threading.Thread(target=run_players_sync, args=(include_stats,))
    thread.daemon = True
    thread.start()
    
    return jsonify({'message': 'Players sync started', 'status': 'running'})


@bp.route('/sync/match-players', methods=['POST'])
@require_auth
def match_players():
    """Match draft players with MantraFootball data"""
    user = request.user
    if not user.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    if sync_status['matching']['running']:
        return jsonify({'error': 'Player matching already running'}), 400
    
    # Start matching in background
    thread = threading.Thread(target=run_player_matching)
    thread.daemon = True
    thread.start()
    
    return jsonify({'message': 'Player matching started', 'status': 'running'})


@bp.route('/sync/status')
@require_auth
def get_sync_status():
    """Get current sync status"""
    user = request.user
    if not user.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    # Add cache status
    cache_status = mantra_store.get_cache_status()
    
    return jsonify({
        'sync_status': sync_status,
        'cache_status': cache_status
    })


@bp.route('/data/tournaments')
@require_auth
def get_tournaments():
    """Get cached tournaments data"""
    tournaments = mantra_store.get_tournaments()
    return jsonify({'tournaments': tournaments, 'total': len(tournaments)})


@bp.route('/data/players')
@require_auth
def get_players():
    """Get cached players data"""
    players = mantra_store.get_players()
    
    # Apply filters if provided
    filters = request.args.to_dict()
    
    if 'tournament_id' in filters:
        tournament_ids = [int(x) for x in filters['tournament_id'].split(',')]
        players = [p for p in players if p.get('mantra_data', {}).get('club', {}).get('tournament_id') in tournament_ids]
    
    if 'position' in filters:
        positions = filters['position'].split(',')
        players = [p for p in players if p.get('position') in positions]
    
    if 'club_id' in filters:
        club_ids = [int(x) for x in filters['club_id'].split(',')]
        players = [p for p in players if p.get('club', {}).get('id') in club_ids]
    
    if 'search' in filters:
        search_term = filters['search'].lower()
        players = [p for p in players if search_term in p.get('name', '').lower() or 
                  search_term in p.get('club', {}).get('name', '').lower()]
    
    return jsonify({'players': players, 'total': len(players)})


@bp.route('/data/matches')
@require_auth
def get_player_matches():
    """Get player matching results"""
    data = mantra_store.load_data('player_matches')
    return jsonify(data if data else {'matches': [], 'unmatched': []})


@bp.route('/cache/clear', methods=['POST'])
@require_auth
def clear_cache():
    """Clear cache data"""
    user = request.user
    if not user.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    cache_type = request.json.get('cache_type') if request.json else None
    mantra_store.clear_cache(cache_type)
    
    return jsonify({'message': f'Cache cleared: {cache_type or "all"}'})


@bp.route('/test-api')
@require_auth
def test_api():
    """Test MantraFootball API connection"""
    user = request.user
    if not user.get('godmode'):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Test basic API call
        tournaments = mantra_store.api.get_tournaments()
        
        return jsonify({
            'status': 'success',
            'message': 'API connection successful',
            'tournaments_count': len(tournaments.get('data', [])),
            'sample_tournament': tournaments.get('data', [{}])[0] if tournaments.get('data') else None
        })
    
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'API connection failed: {str(e)}'
        }), 500
