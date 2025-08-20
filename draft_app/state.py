import os
from .config import (
    UCL_USERS, EPL_USERS, TOP4_USERS,
    UCL_POSITION_LIMITS, EPL_POSITION_LIMITS, TOP4_POSITION_LIMITS,
    UCL_STATE_FILE, EPL_STATE_FILE, TOP4_STATE_FILE,
    UCL_PLAYERS_FILE, UCL_CACHE_DIR
)
from .services import load_json, save_json, parse_ucl_players, load_epl_players

# Глобальные кэши в памяти
ucl_state = None
epl_state = None
top4_state = None
ucl_players = []
epl_players = []

def _default_state(users):
    return {
        'rosters':            {u: [] for u in users},
        'draft_order':        [],
        'current_pick_index': 0,
        'picks':              []
    }

def _build_snake_order(users, rounds_total):
    order = []
    for rnd in range(rounds_total):
        seq = users if rnd % 2 == 0 else list(reversed(users))
        order.extend(seq)
    return order

def init_ucl(app):
    global ucl_state, ucl_players
    # состояние
    state = load_json(UCL_STATE_FILE, default=None)
    if state is None:
        state = _default_state(UCL_USERS)
    ucl_state = state
    # игроки
    pdata = load_json(UCL_PLAYERS_FILE, default={'data': {'value': {'playerList': []}}})
    ucl_players = parse_ucl_players(pdata)
    # порядок
    if not ucl_state['draft_order']:
        total = sum(UCL_POSITION_LIMITS.values())
        ucl_state['draft_order'] = _build_snake_order(UCL_USERS, total)
        save_json(UCL_STATE_FILE, ucl_state)

def init_epl(app):
    global epl_state, epl_players
    # состояние
    state = load_json(EPL_STATE_FILE, default=None)
    if state is None:
        state = _default_state(EPL_USERS)
    epl_state = state
    # игроки
    epl_players = load_epl_players()
    # порядок
    if not epl_state['draft_order']:
        total = sum(EPL_POSITION_LIMITS.values())  # 22
        epl_state['draft_order'] = _build_snake_order(EPL_USERS, total)
        save_json(EPL_STATE_FILE, epl_state)

def init_top4(app):
    global top4_state
    state = load_json(TOP4_STATE_FILE, default=None)
    if state is None:
        state = _default_state(TOP4_USERS)
    top4_state = state
    if not top4_state['draft_order']:
        total = sum(TOP4_POSITION_LIMITS.values())
        top4_state['draft_order'] = _build_snake_order(TOP4_USERS, total)
        save_json(TOP4_STATE_FILE, top4_state)

# Общие хелперы
def save_ucl_state():
    save_json(UCL_STATE_FILE, ucl_state)

def save_epl_state():
    save_json(EPL_STATE_FILE, epl_state)

def user_is_full(roster, limits):
    return len(roster) >= sum(limits.values())

def draft_is_completed(state, limits, n_users):
    total_picks_needed = sum(limits.values()) * n_users
    return state['current_pick_index'] >= total_picks_needed
