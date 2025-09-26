#!/usr/bin/env python3
"""
Скрипт для обновления top4_players.json данными из Google Sheets
"""

import csv
import json
import random
import requests
from typing import Dict, List, Any

def load_current_players() -> Dict[str, Any]:
    """Загружаем оригинальный список игроков для сохранения playerId"""
    try:
        # Используем оригинальный файл для поиска существующих playerId
        with open('data/cache/top4_players_original.json', 'r', encoding='utf-8') as f:
            current_players = json.load(f)
        
        # Создаем словарь для быстрого поиска по имени и клубу
        player_lookup = {}
        for player in current_players:
            key = f"{player['fullName']}_{player['clubName']}"
            player_lookup[key] = player
        
        return player_lookup, current_players
    except Exception as e:
        print(f"Ошибка загрузки оригинальных игроков: {e}")
        print("Используем пустой список...")
        return {}, []

def get_next_player_id(current_players: List[Dict]) -> int:
    """Генерируем следующий уникальный playerId"""
    if not current_players:
        return 300000  # Начальный ID для новых игроков
    
    max_id = max(player.get('playerId', 0) for player in current_players)
    return max_id + 1

def normalize_position(position: str) -> str:
    """Нормализуем позиции"""
    pos_map = {
        'Нп': 'FWD',
        'Пз': 'MID', 
        'Зщ': 'DEF',
        'Вр': 'GK'
    }
    return pos_map.get(position, 'MID')  # По умолчанию MID

def normalize_league(league: str) -> str:
    """Нормализуем названия лиг"""
    league_map = {
        'Bundesliga': 'Bundesliga',
        'La Liga': 'La Liga', 
        'EPL': 'Premier League',
        'Serie A': 'Serie A'
    }
    return league_map.get(league, league)

def determine_league_by_club(club: str) -> str:
    """Определяем лигу по названию клуба"""
    club_lower = club.lower()
    
    # Bundesliga клубы
    bundesliga_clubs = [
        'бавария', 'боруссия д', 'рб лейпциг', 'байер', 'айнтрахт ф', 'хоффенхайм', 
        'санкт-паули', 'вердер', 'фрайбург', 'вольфсбург', 'унион берлин', 
        'боруссия м', 'майнц', 'штутгарт', 'аугсбург', 'кильн', 'гейденхайм', 'холштайн киль'
    ]
    
    # Serie A клубы  
    serie_a_clubs = [
        'ювентус', 'милан', 'интер', 'наполи', 'рома', 'лацио', 'аталанта', 
        'фиорентина', 'болонья', 'торино', 'удинезе', 'сассуоло', 'эмполи',
        'верона', 'специя', 'салернитана', 'дженоа', 'венеция', 'кальяри', 'лечче'
    ]
    
    # La Liga клубы
    la_liga_clubs = [
        'реал мадрид', 'барселона', 'атлетико', 'севилья', 'валенсия', 'вильярреал',
        'реал сосьедад', 'бетис', 'атлетик', 'осасуна', 'сельта', 'райо вальекано',
        'хетафе', 'эспаньол', 'мальорка', 'кадис', 'эльче', 'леванте', 'алавес', 'гранада'
    ]
    
    # Premier League клубы
    premier_league_clubs = [
        'манчестер сити', 'ливerpool', 'челси', 'арсенал', 'манчестер юнайтед',
        'тоттенхэм', 'вест хэм', 'лестер', 'эвертон', 'лидс', 'астон вилла',
        'ньюкасл', 'вулверхэмптон', 'кристал пэлас', 'саутгемптон', 'бернли',
        'уотфорд', 'норвич', 'брайтон', 'бренфорд'
    ]
    
    for club_name in bundesliga_clubs:
        if club_name in club_lower:
            return 'Bundesliga'
            
    for club_name in serie_a_clubs:
        if club_name in club_lower:
            return 'Serie A'
            
    for club_name in la_liga_clubs:
        if club_name in club_lower:
            return 'La Liga'
            
    for club_name in premier_league_clubs:
        if club_name in club_lower:
            return 'Premier League'
    
    # По умолчанию возвращаем Bundesliga (так как больше всего клубов оттуда)
    return 'Bundesliga'

def download_sheets_data() -> str:
    """Скачиваем данные из Google Sheets"""
    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSIhYKZORtV12Qqaf-_KsY_KANI9Y2PHU56TDvELzh29s3ZMALcaM4G2BJMPBvtpae_Q29lH2PzGcK_/pub?gid=1433161548&single=true&output=csv"
    
    try:
        response = requests.get(url, timeout=30, allow_redirects=True)
        response.raise_for_status()
        # Пробуем разные кодировки
        try:
            return response.content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                return response.content.decode('cp1251')
            except UnicodeDecodeError:
                return response.content.decode('latin-1')
    except Exception as e:
        print(f"Ошибка загрузки данных из Google Sheets: {e}")
        return ""

def update_players_from_sheets():
    """Основная функция обновления"""
    print("🔄 Загружаем данные из Google Sheets...")
    
    # Скачиваем данные
    sheets_data = download_sheets_data()
    if not sheets_data:
        print("❌ Не удалось получить данные из Google Sheets")
        return
    
    # Загружаем текущих игроков
    player_lookup, current_players = load_current_players()
    print(f"📊 Загружено {len(current_players)} текущих игроков")
    
    # Парсим CSV данные
    lines = sheets_data.splitlines()
    print(f"📝 Получено {len(lines)} строк данных")
    
    csv_reader = csv.DictReader(lines)
    
    updated_players = []
    new_players_count = 0
    updated_players_count = 0
    next_player_id = get_next_player_id(current_players)
    
    print("🔄 Обрабатываем данные из таблицы...")
    
    row_count = 0
    for row in csv_reader:
        row_count += 1
        # Пропускаем пустые строки
        if not row.get('Имя') or not row.get('Клуб'):
            continue
            
        name = row['Имя'].strip()
        club = row['Клуб'].strip()
        position = normalize_position(row.get('А', '').strip())
        
        # Используем лигу из таблицы, если доступна, иначе определяем по клубу
        league = row.get('League', '').strip()
        if not league:
            league = determine_league_by_club(club)
        else:
            league = normalize_league(league)
        
        if row_count % 500 == 0:  # Показываем прогресс каждые 500 строк
            print(f"Обработано {row_count} строк...")
        
        # Парсим числовые значения
        try:
            popularity = float(row.get('П-ть', '0').replace(',', '.'))
        except:
            popularity = 0.0
            
        try:
            fp_last = float(row.get('Pts', '0').replace(',', '.'))
        except:
            fp_last = 0.0
            
        # Парсим цену из столбца "$"
        try:
            price = float(row.get('$', '0').replace(',', '.'))
        except:
            price = round(5.0 + random.uniform(0, 10), 1)  # Случайная цена если не указана
        
        # Ищем существующего игрока
        lookup_key = f"{name}_{club}"
        existing_player = player_lookup.get(lookup_key)
        
        if existing_player:
            # Обновляем существующего игрока, сохраняя все оригинальные поля
            updated_player = existing_player.copy()
            updated_player.update({
                'popularity': popularity,
                'fp_last': fp_last,
                'position': position,
                'league': league,
                'price': price  # Обновляем цену из таблицы
            })
            updated_players.append(updated_player)
            updated_players_count += 1
        else:
            # Создаем нового игрока
            new_player = {
                'playerId': next_player_id,
                'fullName': name,
                'clubName': club,
                'position': position,
                'league': league,
                'price': price,  # Используем цену из таблицы
                'popularity': popularity,
                'fp_last': fp_last
            }
            updated_players.append(new_player)
            new_players_count += 1
            next_player_id += 1
    
    # Сохраняем обновленный файл
    print("💾 Сохраняем обновленные данные...")
    
    with open('data/cache/top4_players.json', 'w', encoding='utf-8') as f:
        json.dump(updated_players, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Обновление завершено!")
    print(f"📊 Всего игроков: {len(updated_players)}")
    print(f"🆕 Новых игроков: {new_players_count}")
    print(f"🔄 Обновлено игроков: {updated_players_count}")

if __name__ == "__main__":
    update_players_from_sheets()
