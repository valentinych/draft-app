#!/usr/bin/env python
"""Refresh cached Top-4 players with market values from transfermarkt-api."""
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
_json_load = top4_services._json_load
_price_from_api = top4_services._price_from_api
PLAYERS_CACHE = top4_services.PLAYERS_CACHE

def main():
    players = _json_load(PLAYERS_CACHE) or []
    for p in players:
        pid = p.get("playerId")
        if pid is None:
            continue
        p["price"] = _price_from_api(pid)
        sleep(0.5)
    _json_dump_atomic(PLAYERS_CACHE, players)
    print(f"Updated prices for {len(players)} players in {PLAYERS_CACHE}")

if __name__ == "__main__":
    main()
