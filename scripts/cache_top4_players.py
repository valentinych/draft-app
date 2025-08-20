#!/usr/bin/env python
"""Refresh cached Top-4 players for a given season ensuring prices are present."""

from pathlib import Path
from time import sleep
import importlib
import sys
import types

BASE_DIR = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("draft_app")
pkg.__path__ = [str(BASE_DIR / "draft_app")]
sys.modules["draft_app"] = pkg
top4_services = importlib.import_module("draft_app.top4_services")

_json_dump_atomic = top4_services._json_dump_atomic
_price_from_api = top4_services._price_from_api
_fetch_players = top4_services._fetch_players
PLAYERS_CACHE = top4_services.PLAYERS_CACHE


def _ensure_prices(players, attempts: int = 5) -> None:
    """Fill missing prices, retrying up to `attempts` times."""
    for _ in range(attempts):
        missing = [p for p in players if p.get("price") is None]
        if not missing:
            break
        for p in missing:
            pid = p.get("playerId")
            if pid is None:
                continue
            val = _price_from_api(pid)
            if val is not None:
                p["price"] = val
            sleep(0.5)
    for p in players:
        if p.get("price") is None:
            p["price"] = 0.0


def main(season_id: int = 2025) -> None:
    players = _fetch_players(season_id=season_id)
    _ensure_prices(players)
    _json_dump_atomic(PLAYERS_CACHE, players)
    print(f"Cached {len(players)} players for season {season_id} at {PLAYERS_CACHE}")


if __name__ == "__main__":
    main()

