#!/usr/bin/env python3
"""
Исправление составов Тёмы: добавление вратаря в старт для GW11-17
"""
import json
import sys
sys.path.insert(0, '.')
from draft_app.lineup_store import load_lineup, save_lineup
import urllib.request

def get_fpl_players():
    """Получает информацию об игроках из FPL API"""
    url = 'https://fantasy.premierleague.com/api/bootstrap-static/'
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            bootstrap = json.loads(response.read().decode('utf-8'))
            players = bootstrap.get('elements', [])
            element_types = {t['id']: t['singular_name'] for t in bootstrap.get('element_types', [])}
            
            players_dict = {}
            for p in players:
                pid = p.get('id')
                if pid:
                    pos = element_types.get(p.get('element_type'), '?')
                    players_dict[pid] = {
                        'position': pos
                    }
            return players_dict
    except Exception as e:
        print(f'Ошибка загрузки FPL API: {e}')
        return {}

def main():
    print("=" * 80)
    print("ИСПРАВЛЕНИЕ СОСТАВОВ ТЁМЫ: ДОБАВЛЕНИЕ ВРАТАРЯ")
    print("=" * 80)
    print()
    
    fpl_players = get_fpl_players()
    
    # Вратари Тёмы
    tema_gks = [253, 399]  # Dean Henderson, Ederson
    
    for gw in range(11, 18):
        lineup = load_lineup('Тёма', gw, prefer_s3=False)
        if not lineup:
            print(f"  ⚠️  GW{gw}: состав не найден")
            continue
        
        players = lineup.get('players', [])
        bench = lineup.get('bench', [])
        
        # Проверяем, есть ли вратарь в старте
        has_gk_in_start = False
        for pid in players:
            if 1 <= pid <= 1000:
                pos = fpl_players.get(pid, {}).get('position', '?')
                if pos == 'Goalkeeper':
                    has_gk_in_start = True
                    break
        
        if has_gk_in_start:
            print(f"  ✅ GW{gw}: вратарь уже в старте")
            continue
        
        # Ищем вратаря на скамейке
        gk_id = None
        for pid in bench:
            if 1 <= pid <= 1000 and pid in tema_gks:
                pos = fpl_players.get(pid, {}).get('position', '?')
                if pos == 'Goalkeeper':
                    gk_id = pid
                    break
        
        if not gk_id:
            # Берем первого доступного вратаря из ростера
            gk_id = tema_gks[0]
            print(f"  ⚠️  GW{gw}: вратарь не найден на скамейке, используем {gk_id}")
        
        # Удаляем вратаря из скамейки, если он там есть
        if gk_id in bench:
            bench.remove(gk_id)
        
        # Добавляем вратаря в старт (в начало списка)
        if gk_id not in players:
            players.insert(0, gk_id)
            lineup['players'] = players
            lineup['bench'] = bench
            
            # Пересчитываем схему
            def_count = sum(1 for pid in players if 1 <= pid <= 1000 and fpl_players.get(pid, {}).get('position') == 'Defender')
            mid_count = sum(1 for pid in players if 1 <= pid <= 1000 and fpl_players.get(pid, {}).get('position') == 'Midfielder')
            fwd_count = sum(1 for pid in players if 1 <= pid <= 1000 and fpl_players.get(pid, {}).get('position') == 'Forward')
            
            if def_count == 5 and mid_count == 4 and fwd_count == 1:
                lineup['formation'] = "5-4-1"
            elif def_count == 5 and mid_count == 5 and fwd_count == 1:
                lineup['formation'] = "5-5-1"  # Нестандартная, но корректная
            else:
                lineup['formation'] = f"{def_count}-{mid_count}-{fwd_count}"
            
            save_lineup('Тёма', gw, lineup)
            print(f"  ✅ GW{gw}: добавлен вратарь {gk_id}, схема: {lineup['formation']}")
        else:
            print(f"  ℹ️  GW{gw}: вратарь уже в старте")

if __name__ == "__main__":
    main()

