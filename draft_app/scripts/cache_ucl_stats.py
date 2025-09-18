"""Warm UCL popup stats cache for all drafted players."""
from __future__ import annotations

import argparse
import sys
from typing import Set

from draft_app.ucl import _ucl_state_load, _ensure_ucl_state_shape
from draft_app.ucl_stats_store import get_player_stats


def gather_player_ids(state) -> Set[int]:
    seen: Set[int] = set()
    rosters = state.get("rosters") or {}
    for roster in rosters.values():
        if not isinstance(roster, list):
            continue
        for item in roster:
            if not isinstance(item, dict):
                continue
            pid = (
                item.get("playerId")
                or item.get("id")
                or (item.get("player") or {}).get("playerId")
            )
            if pid is None:
                continue
            try:
                seen.add(int(pid))
            except Exception:
                continue
    picks = state.get("picks") or []
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        pid = (
            pick.get("playerId")
            or pick.get("id")
            or (pick.get("player") or {}).get("playerId")
        )
        if pid is None:
            continue
        try:
            seen.add(int(pid))
        except Exception:
            continue
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="limit number of players to prefetch")
    args = parser.parse_args()

    state = _ensure_ucl_state_shape(_ucl_state_load())
    players = sorted(gather_player_ids(state))
    if args.limit is not None:
        players = players[: args.limit]

    print(f"Prefetching stats for {len(players)} players…", flush=True)
    failures = 0
    for idx, pid in enumerate(players, 1):
        try:
            stats = get_player_stats(pid)
            if not stats:
                failures += 1
                print(f"[{idx}/{len(players)}] pid={pid}: empty stats", flush=True)
                continue

            value = stats.get("value")
            if value is None:
                value = (stats.get("data") or {}).get("value")

            if not isinstance(value, dict):
                failures += 1
                print(f"[{idx}/{len(players)}] pid={pid}: missing value section", flush=True)
                continue

            points = value.get("points") or value.get("matchdayPoints") or []
            info = value.get("shortName") or value.get("fullName") or value.get("name") or ""
            print(f"[{idx}/{len(players)}] pid={pid}: {info} points_entries={len(points)}", flush=True)
        except Exception as exc:
            failures += 1
            print(f"[{idx}/{len(players)}] pid={pid}: error {exc}", flush=True)
    if failures:
        print(f"Done with {failures} failures", flush=True)
        return 1
    print("Done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
