#!/usr/bin/env python
"""Fetch top-4 league players with market values and cache to JSON."""
from draft_app.top4_services import _fetch_players, _json_dump_atomic, PLAYERS_CACHE

def main():
    players = _fetch_players()
    _json_dump_atomic(PLAYERS_CACHE, players)
    print(f"Cached {len(players)} players to {PLAYERS_CACHE}")

if __name__ == "__main__":
    main()
