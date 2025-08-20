from __future__ import annotations
from datetime import datetime, date
from functools import lru_cache
from typing import Dict, List
import requests

OPENFOOTBALL_URLS = {
    "Bundesliga": "https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/de.1.json",
    "EPL": "https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/en.1.json",
    "La Liga": "https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/es.1.json",
    "Serie A": "https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/it.1.json",
}

SKIP_ROUNDS = {
    "Bundesliga": [],
    "EPL": [1, 14, 18, 38],
    "La Liga": [1, 6, 36, 38],
    "Serie A": [9, 17, 19, 38],
}

@lru_cache()
def _load_rounds(league: str) -> List[Dict]:
    url = OPENFOOTBALL_URLS.get(league)
    if not url:
        return []
    try:
        data = requests.get(url, timeout=10).json()
    except Exception:
        return []
    rounds: Dict[int, date] = {}
    for m in data.get("matches", []):
        try:
            r = int(str(m.get("round", "")).split()[-1])
            d = datetime.strptime(m.get("date"), "%Y-%m-%d").date()
        except Exception:
            continue
        if r not in rounds or d < rounds[r]:
            rounds[r] = d
    return [{"round": r, "date": rounds[r]} for r in sorted(rounds)]

def build_schedule() -> Dict[str, List[Dict]]:
    today = date.today()
    result: Dict[str, List[Dict]] = {}
    leagues = ["Bundesliga", "EPL", "La Liga", "Serie A"]
    for league in leagues:
        rounds = _load_rounds(league)
        skip_nums = set(SKIP_ROUNDS.get(league, []))
        info: List[Dict] = []
        for rd in rounds:
            if rd["date"] >= today:
                info.append({
                    "round": rd["round"],
                    "date": rd["date"].strftime("%Y-%m-%d"),
                    "skip": rd["round"] in skip_nums,
                })
        if not info:
            for rd in rounds:
                info.append({
                    "round": rd["round"],
                    "date": rd["date"].strftime("%Y-%m-%d"),
                    "skip": rd["round"] in skip_nums,
                })
        for item in info:
            if not item["skip"]:
                item["current"] = True
                break
        result[league] = info
    return result
