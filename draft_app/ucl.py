import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from . import state
from .config import (
    UCL_USERS, UCL_POSITION_LIMITS, UCL_POSITION_MAP,
    POSITION_ORDER
)

bp = Blueprint("ucl", __name__)

def can_pick_ucl(user, plyr):
    # уникальность клуба
    clubs = {x['clubName'] for x in state.ucl_state['rosters'][user]}
    if plyr['clubName'] in clubs:
        return False, 'У вас уже есть игрок этого клуба.'
    # лимит по позиции
    cnt = sum(1 for x in state.ucl_state['rosters'][user] if x['position'] == plyr['position'])
    if cnt >= UCL_POSITION_LIMITS[plyr['position']]:
        return False, f"Максимум {UCL_POSITION_LIMITS[plyr['position']]} игроков на позиции {plyr['position']}."
    return True, ''

@bp.route("/ucl", methods=["GET", "POST"])
def index():
    current_user = session.get('user_name')
    club_filter = request.args.get('club', '')
    pos_filter  = request.args.get('position', '')

    completed = state.draft_is_completed(state.ucl_state, UCL_POSITION_LIMITS, len(UCL_USERS))

    if request.method == "POST":
        if completed:
            flash('Драфт завершён. Пики больше недоступны.', 'warning')
            return redirect(url_for('ucl.index', club=club_filter, position=pos_filter))

        idx = state.ucl_state['current_pick_index']
        next_user = state.ucl_state['draft_order'][idx] if idx < len(state.ucl_state['draft_order']) else None

        if current_user != next_user and not session.get('godmode'):
            flash('Сейчас не ваш ход.', 'error')
            return redirect(url_for('ucl.index', club=club_filter, position=pos_filter))

        acting_user = next_user if session.get('godmode') else current_user
        if state.user_is_full(state.ucl_state['rosters'][acting_user], UCL_POSITION_LIMITS):
            flash('Ваш состав уже заполнен по всем позициям.', 'warning')
            return redirect(url_for('ucl.index', club=club_filter, position=pos_filter))

        pid = int(request.form.get('player_id') or 0)
        plyr = next(x for x in state.ucl_players if x['playerId'] == pid)

        ok, msg = can_pick_ucl(acting_user, plyr)
        if not ok:
            flash(msg, 'error')
        else:
            state.ucl_state['rosters'][acting_user].append(plyr)
            state.ucl_state['picks'].append({'user': acting_user, 'player': plyr})
            state.ucl_state['current_pick_index'] += 1
            state.save_ucl_state()
        return redirect(url_for('ucl.index', club=club_filter, position=pos_filter))

    # GET
    drafted_ids = [rec['player']['playerId'] for rec in state.ucl_state['picks']]
    filtered = [
        p for p in state.ucl_players
        if p['playerId'] not in drafted_ids
        and (not club_filter or p['clubName'] == club_filter)
        and (not pos_filter or p['position'] == pos_filter)
    ]

    idx = state.ucl_state['current_pick_index']
    next_user = state.ucl_state['draft_order'][idx] if (idx < len(state.ucl_state['draft_order'])) else None
    next_round = idx // len(UCL_USERS) + 1 if next_user else None

    return render_template(
        'index.html',
        draft_title='UCL Fantasy Draft',
        players=([] if completed else filtered),
        clubs=sorted({p['clubName'] for p in state.ucl_players}),
        positions=sorted({p['position'] for p in state.ucl_players}),
        club_filter=club_filter,
        pos_filter=pos_filter,
        next_user=None if completed else next_user,
        next_round=None if completed else next_round,
        rosters=state.ucl_state['rosters'],
        position_limits=UCL_POSITION_LIMITS,
        current_user=current_user,
        status_url=url_for('ucl.status'),
        draft_completed=completed
    )

@bp.route("/ucl/status")
def status():
    from .config import UCL_CACHE_DIR
    from .services import load_json

    view = request.args.get('view', 'picks')
    idx = state.ucl_state['current_pick_index']
    next_user = state.ucl_state['draft_order'][idx] if idx < len(state.ucl_state['draft_order']) else None
    next_round = idx // len(UCL_USERS) + 1 if next_user else None
    completed = state.draft_is_completed(state.ucl_state, UCL_POSITION_LIMITS, len(UCL_USERS))

    if view == 'picks':
        pick_list = []
        for i, rec in enumerate(state.ucl_state['picks']):
            pick_list.append({'round': i // len(UCL_USERS) + 1,
                              'user': rec['user'], 'player': rec['player']})
        return render_template(
            'status.html',
            draft_title='UCL Fantasy Draft',
            status_endpoint='ucl.status',
            view='picks',
            pick_list=pick_list,
            next_user=None if completed else next_user,
            next_round=None if completed else next_round,
            draft_completed=completed,
            gw_deadlines={}
        )

    # rosters
    rounds_range = list(range(1, 18))
    position_slots = []
    for pos in ['Goalkeeper', 'Defender', 'Midfielder', 'Forward']:
        for i in range(UCL_POSITION_LIMITS[pos]):
            position_slots.append({'position': pos, 'slot_index': i})

    rosters_points, rosters_sum, pick_numbers, rosters_grouped = {}, {}, {}, {}

    for num, rec in enumerate(ucl_state['picks'], start=1):
        pick_numbers.setdefault(rec['user'], {})[rec['player']['playerId']] = num

    for user in UCL_USERS:
        # сгруппируем по позиции
        grouped = {'Goalkeeper': [], 'Defender': [], 'Midfielder': [], 'Forward': []}
        for p in state.ucl_state['rosters'][user]:
            grouped[p['position']].append(p)
        rosters_grouped[user] = grouped

        rosters_points[user] = {}
        rosters_sum[user] = {}
        for plyr in state.ucl_state['rosters'][user]:
            pid = plyr['playerId']
            cache = os.path.join(UCL_CACHE_DIR, f'popupstats_70_{pid}.json')
            pts_map, total = {}, 0
            j = load_json(cache, default=None)
            if j:
                dv = j.get('data', {}).get('value', {})
                for itm in dv.get('matchdayPoints', []):
                    if 'mdId' in itm:
                        pts_map[str(itm['mdId'])] = itm.get('tPoints', '-')
            lst = []
            for r in rounds_range:
                v = pts_map.get(str(r), '-')
                lst.append(v)
                try:
                    total += int(v)
                except Exception:
                    pass
            rosters_points[user][pid] = lst
            rosters_sum[user][pid] = total

    return render_template(
        'status.html',
        draft_title='UCL Fantasy Draft',
        status_endpoint='ucl.status',
        view='rosters',
        users=UCL_USERS,
        rosters=ucl_state['rosters'],
        rosters_grouped=rosters_grouped,
        position_slots=position_slots,
        rounds_range=rounds_range,
        rosters_points=rosters_points,
        rosters_sum=rosters_sum,
        pick_numbers=pick_numbers,
        next_user=None if completed else next_user,
        next_round=None if completed else next_round,
        draft_completed=completed,
        gw_deadlines={}
    )
