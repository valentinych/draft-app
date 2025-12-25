#!/usr/bin/env python3
"""
Скрипт для применения восстановленных трансферов к draft_state_epl.json
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

def get_player_id(player: dict) -> int:
    return int(player.get("playerId") or player.get("id") or 0)

def find_player_in_roster(roster: List[dict], player_id: int) -> dict:
    """Найти игрока в ростре по ID"""
    for p in roster:
        if get_player_id(p) == player_id:
            return p
    return {}

def apply_transfers_to_state(state_file: Path, reference_file: Path, output_file: Path):
    """Применяет восстановленные трансферы к state и логирует их"""
    
    # Загружаем текущий state
    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    # Загружаем reference файл (после GW10) для получения информации о новых игроках
    reference_state = None
    if reference_file and reference_file.exists():
        with open(reference_file, 'r', encoding='utf-8') as f:
            reference_state = json.load(f)
    
    # Восстанавливаем оригинальные ростеры из picks
    original_rosters: Dict[str, List[dict]] = {}
    for pick in state.get("picks", []):
        manager = pick.get("user")
        if not manager:
            continue
        if manager not in original_rosters:
            original_rosters[manager] = []
        player = pick.get("player")
        if player:
            original_rosters[manager].append(dict(player))
    
    # Инициализируем transfer.history если её нет
    transfer_data = state.setdefault("transfer", {})
    history = transfer_data.setdefault("history", [])
    
    # Очищаем существующую историю перед применением восстановленных трансферов
    print(f"Очищаем существующую историю ({len(history)} записей)...")
    history.clear()
    
    # Определяем трансферы после GW3
    gw3_transfers = {
        "Андрей": [
            {"out": 491, "out_name": "Sandro Tonali", "in": 83, "in_name": "Dango Ouattara"},
            {"out": 677, "out_name": "Evann Guessand", "in": 389, "in_name": "Harvey Elliott"},
        ],
        "Женя": [
            {"out": 655, "out_name": "Fábio Soares Silva", "in": 726, "in_name": "Randal Kolo Muani"},
        ],
        "Ксана": [
            {"out": 663, "out_name": "Jhon Arias", "in": 242, "in_name": "Kiernan Dewsbury-Hall"},
        ],
        "Макс": [
            {"out": 158, "out_name": "Georginio Rutter", "in": 569, "in_name": "Cristian Romero"},
            {"out": 610, "out_name": "Aaron Wan-Bissaka", "in": 717, "in_name": "Xavi Simons"},
        ],
        "Руслан": [
            {"out": 239, "out_name": "Jamie Bynoe-Gittens", "in": 478, "in_name": "Kieran Trippier"},
            {"out": 672, "out_name": "Jorrel Hato", "in": 516, "in_name": "Callum Hudson-Odoi"},
        ],
        "Саша": [
            {"out": 526, "out_name": "Igor Jesus Maciel da Cruz", "in": 261, "in_name": "Chris Richards"},
            {"out": 607, "out_name": "Nayef Aguerd", "in": 714, "in_name": "Nick Woltemade"},
        ],
    }
    
    # Определяем трансферы после GW10
    gw10_transfers = {
        "Андрей": [
            {"out": 507, "out_name": "Ola Aina", "in": 411, "in_name": "Nico O'Reilly"},
        ],
        "Женя": [
            {"out": 48, "out_name": "Youri Tielemans", "in": 205, "in_name": "Josh Cullen"},
        ],
        "Ксана": [
            {"out": 669, "out_name": "Dan Ndoye", "in": 668, "in_name": "Granit Xhaka"},
        ],
        "Макс": [
            {"out": 11, "out_name": "Benjamin White", "in": 36, "in_name": "Matty Cash"},
        ],
        "Руслан": [
            {"out": 525, "out_name": "Chris Wood", "in": 100, "in_name": "Junior Kroupi"},
        ],
        "Саша": [
            {"out": 353, "out_name": "Daniel James", "in": 20, "in_name": "Leandro Trossard"},
        ],
        "Сергей": [
            {"out": 583, "out_name": "Dejan Kulusevski", "in": 673, "in_name": "Palhinha"},
        ],
        "Тёма": [
            {"out": 680, "out_name": "Armando Broja", "in": 365, "in_name": "Lucas Nmecha"},
        ],
    }
    
    # Нужно получить информацию о новых игроках из текущего ростра или из reference файла
    current_rosters = state.get("rosters", {})
    reference_rosters = reference_state.get("rosters", {}) if reference_state else {}
    
    # Функция для получения информации об игроке
    def get_player_info(manager: str, player_id: int, rosters: Dict[str, List[dict]], ref_rosters: Dict[str, List[dict]] = None) -> dict:
        """Получить информацию об игроке из ростра"""
        # Сначала ищем в reference ростре (после GW10)
        if ref_rosters:
            roster = ref_rosters.get(manager, [])
            player = find_player_in_roster(roster, player_id)
            if player:
                return dict(player)
        
        # Затем в текущем ростре
        roster = rosters.get(manager, [])
        player = find_player_in_roster(roster, player_id)
        if player:
            return dict(player)
        
        # Если не найден, ищем в оригинальном
        original_roster = original_rosters.get(manager, [])
        player = find_player_in_roster(original_roster, player_id)
        if player:
            return dict(player)
        return {}
    
    # Применяем трансферы после GW3
    print("Применяем трансферы после GW3...")
    rosters_after_gw3 = {}
    for manager in original_rosters.keys():
        roster = list(original_rosters[manager])
        transfers = gw3_transfers.get(manager, [])
        for transfer in transfers:
            out_id = transfer["out"]
            in_id = transfer["in"]
            
            # Удаляем старого игрока
            roster = [p for p in roster if get_player_id(p) != out_id]
            
            # Добавляем нового игрока (используем reference_rosters как основной источник)
            in_player = get_player_info(manager, in_id, current_rosters, reference_rosters)
            if not in_player:
                print(f"  ⚠️  Предупреждение: не найден игрок {in_id} ({transfer['in_name']}) для {manager}")
                # Создаем минимальную запись
                in_player = {
                    "playerId": in_id,
                    "fullName": transfer["in_name"],
                    "position": "UNKNOWN",  # Нужно будет заполнить вручную
                    "price": 0.0,
                }
            else:
                in_player = dict(in_player)
            
            # Добавляем нового игрока в ростер
            roster.append(in_player)
            
            # Логируем трансфер
            out_player = find_player_in_roster(original_rosters[manager], out_id)
            event = {
                "gw": 3,
                "round": 1,  # Первый раунд трансферного окна GW3
                "manager": manager,
                "out": out_id,
                "out_player": dict(out_player) if out_player else None,
                "in": in_player,
                "ts": datetime.utcnow().isoformat(timespec="seconds"),
            }
            history.append(event)
            print(f"  {manager}: {transfer['out_name']} → {transfer['in_name']}")
        
        rosters_after_gw3[manager] = roster
    
    # Применяем трансферы после GW10
    print("\nПрименяем трансферы после GW10...")
    for manager in rosters_after_gw3.keys():
        roster = list(rosters_after_gw3[manager])
        transfers = gw10_transfers.get(manager, [])
        for transfer in transfers:
            out_id = transfer["out"]
            in_id = transfer["in"]
            
            # Удаляем старого игрока
            roster = [p for p in roster if get_player_id(p) != out_id]
            
            # Добавляем нового игрока (ищем в reference_rosters, так как там финальное состояние после GW10)
            in_player = get_player_info(manager, in_id, current_rosters, reference_rosters)
            if not in_player:
                print(f"  ⚠️  Предупреждение: не найден игрок {in_id} ({transfer['in_name']}) для {manager}")
                # Создаем минимальную запись
                in_player = {
                    "playerId": in_id,
                    "fullName": transfer["in_name"],
                    "position": "UNKNOWN",  # Нужно будет заполнить вручную
                    "price": 0.0,
                }
            else:
                in_player = dict(in_player)
            
            # Добавляем нового игрока в ростер
            roster.append(in_player)
            
            # Логируем трансфер
            out_player = find_player_in_roster(rosters_after_gw3[manager], out_id)
            event = {
                "gw": 10,
                "round": 1,  # Первый раунд трансферного окна GW10
                "manager": manager,
                "out": out_id,
                "out_player": dict(out_player) if out_player else None,
                "in": in_player,
                "ts": datetime.utcnow().isoformat(timespec="seconds"),
            }
            history.append(event)
            print(f"  {manager}: {transfer['out_name']} → {transfer['in_name']}")
        
        # Обновляем ростер в state
        state.setdefault("rosters", {})[manager] = roster
    
    # Сохраняем обновленную историю
    transfer_data["history"] = history
    
    # Сохраняем обновленный state
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Обновленный state сохранен в {output_file}")
    print(f"📊 Всего трансферов в истории: {len(history)}")

if __name__ == "__main__":
    base_dir = Path(__file__).parent.parent
    state_file = base_dir / "draft_state_epl.json"
    reference_file = Path("/Users/ruslan.aharodnik/Downloads/draft_state_epl (10) (1).json")
    output_file = base_dir / "draft_state_epl.json"
    
    if not state_file.exists():
        print(f"❌ Файл {state_file} не найден!")
        exit(1)
    
    apply_transfers_to_state(state_file, reference_file, output_file)

