#!/usr/bin/env python3
"""
Test script to verify Top-4 score calculation, especially Clean Sheet logic
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from draft_app.mantra_routes import _calc_score_breakdown
from draft_app.api_football_score_converter import convert_api_football_stats_to_top4_format

def test_clean_sheet_calculation():
    """Test Clean Sheet calculation for different positions"""
    print("=" * 80)
    print("ТЕСТИРОВАНИЕ ПОДСЧЕТА ОЧКОВ И CLEAN SHEET")
    print("=" * 80)
    
    # Test case 1: Goalkeeper with clean sheet
    print("\n1. Тест: Вратарь с Clean Sheet (60+ минут, 0 пропущенных голов)")
    api_stats = {
        "games": {"minutes": 90},
        "goals": {"total": 0, "conceded": 0, "assists": 0, "saves": 6},
        "cards": {"yellow": 0, "red": 0},
    }
    fixture_data = {
        "teams": {"home": {"id": 1}, "away": {"id": 2}},
        "goals": {"home": 2, "away": 0},  # Team 1 (home) didn't concede
    }
    stat = convert_api_football_stats_to_top4_format(
        api_stats, "GK", fixture_data=fixture_data, team_id=1
    )
    score, breakdown = _calc_score_breakdown(stat, "GK")
    print(f"   Clean Sheet: {stat.get('cleansheet')}")
    print(f"   Очки: {score}")
    print(f"   Breakdown: {[b['label'] for b in breakdown]}")
    assert stat.get("cleansheet") == True, "Clean Sheet должен быть True"
    assert score >= 6, f"Очки должны быть >= 6 (2 за минуты + 4 за CS + 2 за сейвы), получено {score}"
    print("   ✅ PASSED")
    
    # Test case 2: Defender with clean sheet
    print("\n2. Тест: Защитник с Clean Sheet (60+ минут, 0 пропущенных голов)")
    api_stats = {
        "games": {"minutes": 90},
        "goals": {"total": 0, "conceded": 0, "assists": 0, "saves": 0},
        "cards": {"yellow": 0, "red": 0},
    }
    fixture_data = {
        "teams": {"home": {"id": 1}, "away": {"id": 2}},
        "goals": {"home": 1, "away": 0},  # Team 1 (home) didn't concede
    }
    stat = convert_api_football_stats_to_top4_format(
        api_stats, "DEF", fixture_data=fixture_data, team_id=1
    )
    score, breakdown = _calc_score_breakdown(stat, "DEF")
    print(f"   Clean Sheet: {stat.get('cleansheet')}")
    print(f"   Очки: {score}")
    print(f"   Breakdown: {[b['label'] for b in breakdown]}")
    assert stat.get("cleansheet") == True, "Clean Sheet должен быть True"
    assert score == 6, f"Очки должны быть 6 (2 за минуты + 4 за CS), получено {score}"
    print("   ✅ PASSED")
    
    # Test case 3: Midfielder with clean sheet
    print("\n3. Тест: Полузащитник с Clean Sheet (60+ минут, 0 пропущенных голов)")
    api_stats = {
        "games": {"minutes": 90},
        "goals": {"total": 0, "conceded": 0, "assists": 0, "saves": 0},
        "cards": {"yellow": 0, "red": 0},
    }
    fixture_data = {
        "teams": {"home": {"id": 1}, "away": {"id": 2}},
        "goals": {"home": 3, "away": 0},  # Team 1 (home) didn't concede
    }
    stat = convert_api_football_stats_to_top4_format(
        api_stats, "MID", fixture_data=fixture_data, team_id=1
    )
    score, breakdown = _calc_score_breakdown(stat, "MID")
    print(f"   Clean Sheet: {stat.get('cleansheet')}")
    print(f"   Очки: {score}")
    print(f"   Breakdown: {[b['label'] for b in breakdown]}")
    assert stat.get("cleansheet") == True, "Clean Sheet должен быть True"
    assert score == 3, f"Очки должны быть 3 (2 за минуты + 1 за CS), получено {score}"
    print("   ✅ PASSED")
    
    # Test case 4: No clean sheet (team conceded)
    print("\n4. Тест: Вратарь БЕЗ Clean Sheet (команда пропустила голы)")
    api_stats = {
        "games": {"minutes": 90},
        "goals": {"total": 0, "conceded": 2, "assists": 0, "saves": 3},
        "cards": {"yellow": 0, "red": 0},
    }
    fixture_data = {
        "teams": {"home": {"id": 1}, "away": {"id": 2}},
        "goals": {"home": 1, "away": 2},  # Team 1 (home) conceded 2 goals
    }
    stat = convert_api_football_stats_to_top4_format(
        api_stats, "GK", fixture_data=fixture_data, team_id=1
    )
    score, breakdown = _calc_score_breakdown(stat, "GK")
    print(f"   Clean Sheet: {stat.get('cleansheet')}")
    print(f"   Очки: {score}")
    print(f"   Breakdown: {[b['label'] for b in breakdown]}")
    assert stat.get("cleansheet") == False, "Clean Sheet должен быть False"
    # Расчет: 2 (минуты >= 60) + 1 (сейвы: 3 // 3) - 1 (пропущенные: 2 // 2) = 2
    assert score == 2, f"Очки должны быть 2 (2 за минуты + 1 за сейвы - 1 за пропущенные голы), получено {score}"
    print("   ✅ PASSED")
    
    # Test case 5: Player with less than 60 minutes (no clean sheet even if team didn't concede)
    print("\n5. Тест: Игрок < 60 минут (Clean Sheet не засчитывается)")
    api_stats = {
        "games": {"minutes": 45},
        "goals": {"total": 0, "conceded": 0, "assists": 0, "saves": 0},
        "cards": {"yellow": 0, "red": 0},
    }
    fixture_data = {
        "teams": {"home": {"id": 1}, "away": {"id": 2}},
        "goals": {"home": 1, "away": 0},  # Team 1 (home) didn't concede
    }
    stat = convert_api_football_stats_to_top4_format(
        api_stats, "DEF", fixture_data=fixture_data, team_id=1
    )
    score, breakdown = _calc_score_breakdown(stat, "DEF")
    print(f"   Clean Sheet: {stat.get('cleansheet')}")
    print(f"   Очки: {score}")
    print(f"   Breakdown: {[b['label'] for b in breakdown]}")
    # Clean sheet should be False because player played < 60 minutes
    assert stat.get("cleansheet") == False, "Clean Sheet должен быть False (< 60 минут)"
    assert score == 1, f"Очки должны быть 1 (1 за минуты < 60), получено {score}"
    print("   ✅ PASSED")
    
    print("\n" + "=" * 80)
    print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
    print("=" * 80)

if __name__ == "__main__":
    try:
        test_clean_sheet_calculation()
    except AssertionError as e:
        print(f"\n❌ ТЕСТ ПРОВАЛЕН: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

