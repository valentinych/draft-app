"""Warm UCL popup stats cache for all drafted players."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Set

from draft_app.ucl import _ucl_state_load, _ensure_ucl_state_shape
from draft_app.ucl_stats_store import CACHE_DIR, get_player_stats


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


def _cache_status(cache_path: Path, before_mtime: Optional[float]) -> tuple[str, Optional[int]]:
    if not cache_path.exists():
        return ("missing", None)
    stat = cache_path.stat()
    if before_mtime is None:
        state = "written"
    elif stat.st_mtime > (before_mtime + 1e-6):
        state = "updated"
    else:
        state = "unchanged"
    return (state, stat.st_size)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="limit number of players to prefetch")
    parser.add_argument(
        "--player",
        action="append",
        type=int,
        help="explicit playerId to warm; repeat to warm multiple players",
    )
    args = parser.parse_args()

    state = _ensure_ucl_state_shape(_ucl_state_load())
    if args.player:
        players = sorted({int(pid) for pid in args.player if pid is not None})
    else:
        players = sorted(gather_player_ids(state))
    if args.limit is not None:
        players = players[: args.limit]

    print(f"Prefetching stats for {len(players)} players…", flush=True)
    failures = 0
    for idx, pid in enumerate(players, 1):
        cache_path = CACHE_DIR / f"{pid}.json"
        before_mtime = None
        try:
            before_mtime = cache_path.stat().st_mtime
        except Exception:
            before_mtime = None
        try:
            stats = get_player_stats(pid)
            cache_state, cache_size = _cache_status(cache_path, before_mtime)
            if not stats:
                failures += 1
                print(
                    f"[{idx}/{len(players)}] pid={pid}: empty stats cache={cache_state}",
                    flush=True,
                )
                continue

            value = stats.get("value")
            if value is None:
                value = (stats.get("data") or {}).get("value")

            if not isinstance(value, dict):
                failures += 1
                print(
                    f"[{idx}/{len(players)}] pid={pid}: missing value section cache={cache_state}",
                    flush=True,
                )
                continue

            points = value.get("points") or value.get("matchdayPoints") or []
            info = value.get("shortName") or value.get("fullName") or value.get("name") or "<no-name>"
            cache_tail = f" cache={cache_state}"
            if cache_size is not None:
                cache_tail = f"{cache_tail} size={cache_size}"
            print(
                f"[{idx}/{len(players)}] pid={pid}: {info} points_entries={len(points)}{cache_tail}",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            cache_state, cache_size = _cache_status(cache_path, before_mtime)
            cache_tail = f" cache={cache_state}"
            if cache_size is not None:
                cache_tail = f"{cache_tail} size={cache_size}"
            print(
                f"[{idx}/{len(players)}] pid={pid}: error {exc}{cache_tail}",
                flush=True,
            )
    if failures:
        print(f"Done with {failures} failures", flush=True)
        return 1
    print("Done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
