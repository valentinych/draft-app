from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from . import state
from .config import EPL_USERS, EPL_POSITION_LIMITS
from .services import epl_deadlines_window

bp = Blueprint("epl", __name__)

def can_pick_epl(user, plyr):
    # В EPL нет ограничения "не более одного игрока из клуба"
    cnt = sum(1 for x in state.epl_state['rosters'][user] if x['position'] == plyr['position'])
    if cnt >= EPL_POSITION_LIMITS[plyr['position']]:
        return False, f"Максимум {EPL_POSITION_LIMITS[plyr['position']]} игроков на позиции {plyr['position']}."
    return True, ''

@bp.route("/epl", methods=["GET", "POST"])
def index():
    current_user = session.get('user_name')
    club_filter = request.args.get('club', '')
    pos_filter  = request.args.get('position', '')

    completed = state.draft_is_completed(state.epl_state, EPL_POSITION_LIMITS, len(EPL_USERS))

    if request.method == "POST":
        if completed:
            flash('Драфт завершён. Пики больше недоступны.', 'warning')
            return redirect(url_for('epl.index', club=club_filter, position=pos_filter))

        idx = state.epl_state['current_pick_index']
        next_user = state.epl_state['draft_order'][idx] if idx < len(state.epl_state['draft_order']) else None

        if current_user != next_user and not session.get('godmode'):
            flash('Сейчас не ваш ход.', 'error')
            return redirect(url_for('epl.index', club=club_filter, position=pos_filter))

        acting_user = next_user if session.get('godmode') else current_user
        if state.user_is_full(state.epl_state['rosters'][acting_user], EPL_POSITION_LIMITS):
            flash('Ваш состав уже заполнен по всем позициям.', 'warning')
            return redirect(url_for('epl.index', club=club_filter, position=pos_filter))

        pid = int(request.form.get('player_id') or 0)
        plyr = next(x for x in state.epl_players if x['playerId'] == pid)

        ok, msg = can_pick_epl(acting_user, plyr)
        if not ok:
            flash(msg, 'error')
        else:
            state.epl_state['rosters'][acting_user].append(plyr)
            state.epl_state['picks'].append({'user': acting_user, 'player': plyr})
            state.epl_state['current_pick_index'] += 1
            state.save_epl_state()
        return redirect(url_for('epl.index', club=club_filter, position=pos_filter))

    # GET
    drafted_ids = [rec['player']['playerId'] for rec in state.epl_state['picks']]
    filtered = [
        p for p in state.epl_players
        if p['playerId'] not in drafted_ids
        and (not club_filter or p['clubName'] == club_filter)
        and (not pos_filter or p['position'] == pos_filter)
    ]
    filtered.sort(key=lambda x: x.get('price', 0.0), reverse=True)

    idx = state.epl_state['current_pick_index']
    next_user = state.epl_state['draft_order'][idx] if (idx < len(state.epl_state['draft_order'])) else None
    next_round = idx // len(EPL_USERS) + 1 if next_user else None

    return render_template(
        'index.html',
        draft_title='EPL Fantasy Draft',
        players=([] if completed else filtered),
        clubs=sorted({p['clubName'] for p in state.epl_players}),
        positions=sorted({p['position'] for p in state.epl_players}),
        club_filter=club_filter,
        pos_filter=pos_filter,
        next_user=None if completed else next_user,
        next_round=None if completed else next_round,
        rosters=state.epl_state['rosters'],
        position_limits=EPL_POSITION_LIMITS,
        current_user=current_user,
        status_url=url_for('epl.status'),
        draft_completed=completed
    )

@bp.route("/epl/status")
def status():
    view = request.args.get('view', 'picks')
    idx = state.epl_state['current_pick_index']
    next_user = state.epl_state['draft_order'][idx] if idx < len(state.epl_state['draft_order']) else None
    next_round = idx // len(EPL_USERS) + 1 if next_user else None
    completed = state.draft_is_completed(state.epl_state, EPL_POSITION_LIMITS, len(EPL_USERS))

    if view == 'picks':
        pick_list = []
        for i, rec in enumerate(state.epl_state['picks']):
            pick_list.append({'round': i // len(EPL_USERS) + 1,
                              'user': rec['user'], 'player': rec['player']})
        return render_template(
            'status.html',
            draft_title='EPL Fantasy Draft',
            status_endpoint='epl.status',
            view='picks',
            pick_list=pick_list,
            next_user=None if completed else next_user,
            next_round=None if completed else next_round,
            draft_completed=completed,
            gw_deadlines={}
        )

    rounds_range, gw_deadlines = epl_deadlines_window()
    position_slots = []
    for pos in ['Goalkeeper', 'Defender', 'Midfielder', 'Forward']:
        for i in range(EPL_POSITION_LIMITS[pos]):
            position_slots.append({'position': pos, 'slot_index': i})

    rosters_points, rosters_sum, pick_numbers, rosters_grouped = {}, {}, {}, {}
    for num, rec in enumerate(state.epl_state['picks'], start=1):
        pick_numbers.setdefault(rec['user'], {})[rec['player']['playerId']] = num

    for user in EPL_USERS:
        grouped = {'Goalkeeper': [], 'Defender': [], 'Midfielder': [], 'Forward': []}
        for p in state.epl_state['rosters'][user]:
            grouped[p['position']].append(p)
        rosters_grouped[user] = grouped
        rosters_points[user] = {}
        rosters_sum[user] = {}
        for plyr in state.epl_state['rosters'][user]:
            pid = plyr['playerId']
            rosters_points[user][pid] = ['-'] * len(rounds_range)
            rosters_sum[user][pid] = 0

    return render_template(
        'status.html',
        draft_title='EPL Fantasy Draft',
        status_endpoint='epl.status',
        view='rosters',
        users=EPL_USERS,
        rosters=state.epl_state['rosters'],
        rosters_grouped=rosters_grouped,
        position_slots=position_slots,
        rounds_range=rounds_range,
        rosters_points=rosters_points,
        rosters_sum=rosters_sum,
        pick_numbers=pick_numbers,
        next_user=None if completed else next_user,
        next_round=None if completed else next_round,
        draft_completed=completed,
        gw_deadlines=gw_deadlines
    )
