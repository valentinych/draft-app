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

def _choose_skip(other_dates: List[date], bundes_dates: List[date]) -> List[int]:
    n = len(other_dates)
    m = len(bundes_dates)
    dp = [[float("inf")]*(m+1) for _ in range(n+1)]
    path = [[None]*(m+1) for _ in range(n+1)]
    dp[0][0] = 0.0
    for i in range(1, n+1):
        dp[i][0] = 0.0
        path[i][0] = "skip"
    for i in range(1, n+1):
        for j in range(1, min(i, m)+1):
            cost = abs((other_dates[i-1] - bundes_dates[j-1]).days)
            if dp[i-1][j-1] + cost < dp[i][j]:
                dp[i][j] = dp[i-1][j-1] + cost
                path[i][j] = "align"
            if dp[i-1][j] <= dp[i][j]:
                dp[i][j] = dp[i-1][j]
                path[i][j] = "skip"
    i, j = n, m
    skips: List[int] = []
    while i > 0 or j > 0:
        act = path[i][j]
        if act == "align":
            i -= 1
            j -= 1
        else:
            skips.append(i)
            i -= 1
    skips.sort()
    return skips

def build_schedule() -> Dict[str, List[Dict]]:
    today = date.today()
    bundes = _load_rounds("Bundesliga")
    bundes_dates = [r["date"] for r in bundes]
    result: Dict[str, List[Dict]] = {}
    leagues = ["Bundesliga", "EPL", "La Liga", "Serie A"]
    for league in leagues:
        rounds = _load_rounds(league)
        dates = [r["date"] for r in rounds]
        skip_idx = _choose_skip(dates, bundes_dates) if league != "Bundesliga" else []
        info: List[Dict] = []
        for idx, rd in enumerate(rounds, start=1):
            if rd["date"] >= today:
                info.append({
                    "round": rd["round"],
                    "date": rd["date"].strftime("%Y-%m-%d"),
                    "skip": idx in skip_idx,
                })
        if not info:
            for idx, rd in enumerate(rounds, start=1):
                info.append({
                    "round": rd["round"],
                    "date": rd["date"].strftime("%Y-%m-%d"),
                    "skip": idx in skip_idx,
                })
        for item in info:
            if not item["skip"]:
                item["current"] = True
                break
        result[league] = info
    return result
