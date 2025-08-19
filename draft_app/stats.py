import os
from flask import Blueprint, render_template, flash, redirect, url_for
from . import state
from .config import UCL_CACHE_DIR
from .services import HTTP_SESSION, HEADERS_GENERIC, load_json, save_json

bp = Blueprint("stats", __name__)

@bp.route("/stats/<int:pid>")
def index(pid):
    plyr = next((p for p in state.ucl_players if p['playerId'] == pid), None)
    if not plyr:
        flash('Игрок не найден', 'error')
        return redirect(url_for('ucl.index'))

    cache_path = os.path.join(UCL_CACHE_DIR, f'popupstats_70_{pid}.json')
    popup_url  = f'https://gaming.uefa.com/en/uclfantasy/services/feeds/popupstats/popupstats_70_{pid}.json'

    data = load_json(cache_path, default=None)
    if data is None:
        try:
            r = HTTP_SESSION.get(popup_url, headers=HEADERS_GENERIC, timeout=10)
            r.raise_for_status()
            data = r.json()
            save_json(cache_path, data)
        except Exception:
            data = {}

    md_points, md_stats = {}, {}
    val = data.get('data', {}).get('value', {})
    for item in val.get('matchdayPoints', []):
        if 'mdId' in item:
            md_points[str(item['mdId'])] = item.get('tPoints', '-')
    for item in val.get('matchdayStats', []):
        if 'mdId' in item:
            k = str(item['mdId'])
            md_stats[k] = {
                'gS': item.get('gS', '-'),
                'gA': item.get('gA', '-'),
                'cS': item.get('cS', '-'),
                'mOM': item.get('mOM', '-'),
                'yC': item.get('yC', '-'),
                'rC': item.get('rC', '-'),
                'oG': item.get('oG', '-'),
                'oF': item.get('oF', '-'),
            }

    stats_list = []
    for md in range(1, 18):
        k = str(md)
        stats_list.append({
            'mdId': md,
            'tPoints': md_points.get(k, '-'),
            'gS': md_stats.get(k, {}).get('gS', '-'),
            'gA': md_stats.get(k, {}).get('gA', '-'),
            'cS': md_stats.get(k, {}).get('cS', '-'),
            'mOM': md_stats.get(k, {}).get('mOM', '-'),
            'yC': md_stats.get(k, {}).get('yC', '-'),
            'rC': md_stats.get(k, {}).get('rC', '-'),
            'oG': md_stats.get(k, {}).get('oG', '-'),
            'oF': md_stats.get(k, {}).get('oF', '-'),
        })

    return render_template('stats.html', player=plyr, stats_list=stats_list)
