#!/usr/bin/env python3
"""
Обновить finished_matchdays в draft_state_ucl.json для расчета результатов.

Использование:
    python3 scripts/update_ucl_finished_matchdays.py 4 5 6
    python3 scripts/update_ucl_finished_matchdays.py --all  # добавить все туры до текущего
"""
import json
import sys
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).parent.parent
STATE_PATH = BASE_DIR / "draft_state_ucl.json"


def update_finished_matchdays(matchdays: List[int]) -> None:
    """Добавить указанные matchdays в finished_matchdays."""
    if not STATE_PATH.exists():
        print(f"❌ Файл {STATE_PATH} не найден")
        sys.exit(1)
    
    with open(STATE_PATH, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    finished = state.get("finished_matchdays", [])
    original = finished.copy()
    
    for md in matchdays:
        if md not in finished:
            finished.append(md)
    
    finished.sort()
    state["finished_matchdays"] = finished
    
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    added = [md for md in matchdays if md not in original]
    if added:
        print(f"✅ Добавлены туры в finished_matchdays: {added}")
    else:
        print(f"ℹ️  Все указанные туры уже были в finished_matchdays")
    
    print(f"📊 Текущие finished_matchdays: {finished}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        # Добавить все туры до текущего (1-8)
        matchdays = list(range(1, 9))
    else:
        # Парсим туры из аргументов
        matchdays = []
        for arg in sys.argv[1:]:
            try:
                md = int(arg)
                if 1 <= md <= 8:
                    matchdays.append(md)
                else:
                    print(f"⚠️  Пропущен тур {md} (должен быть от 1 до 8)")
            except ValueError:
                print(f"⚠️  Пропущен неверный аргумент: {arg}")
        
        if not matchdays:
            print("Использование:")
            print("  python3 scripts/update_ucl_finished_matchdays.py 4 5 6")
            print("  python3 scripts/update_ucl_finished_matchdays.py --all")
            sys.exit(1)
    
    update_finished_matchdays(matchdays)


if __name__ == "__main__":
    main()

