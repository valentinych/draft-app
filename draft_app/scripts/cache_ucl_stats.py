"""Warm UCL popup stats cache for all drafted players."""
from __future__ import annotations

import argparse
import sys
from typing import Set

from draft_app.ucl import _ucl_state_load, _ensure_ucl_state_shape
from draft_app.ucl_stats_store import refresh_players_batch


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

    summary = refresh_players_batch(players)
    bucket = summary.get("bucket")
    if bucket:
        print(f"Using S3 bucket: {bucket}", flush=True)
    else:
        print("S3 bucket is not configured; stats will only be cached locally.", flush=True)

    total = summary.get("total", 0)
    results = summary.get("results", [])
    print(f"Prefetching stats for {total} players…", flush=True)

    for idx, item in enumerate(results, 1):
        pid = item.get("player_id")
        cache_state = item.get("cache_state", "?")
        cache_size = item.get("cache_size")
        key = item.get("s3_key")
        target = f"s3://{bucket}/{key}" if bucket and key else "local cache"
        cache_tail = f" cache={cache_state}"
        if cache_size is not None:
            cache_tail = f"{cache_tail} size={cache_size}"
        cache_tail_str = f" {cache_tail.strip()}" if cache_tail else ""

        error = item.get("error")
        if error == "empty":
            print(f"[{idx}/{total}] pid={pid}: empty stats{cache_tail_str} target={target}", flush=True)
        elif error == "missing_value":
            print(f"[{idx}/{total}] pid={pid}: missing value section{cache_tail_str} target={target}", flush=True)
        elif error == "exception":
            print(
                f"[{idx}/{total}] pid={pid}: error {item.get('exception')}{cache_tail_str} target={target}",
                flush=True,
            )
        else:
            name = item.get("name") or "<no-name>"
            points_entries = item.get("points_entries") or 0
            suffix = f" s3={target}" if bucket and key else ""
            print(f"[{idx}/{total}] pid={pid}: {name} points_entries={points_entries}{cache_tail_str}{suffix}", flush=True)

    failures = summary.get("failures", 0)
    if failures:
        print(f"Done with {failures} failures", flush=True)
        return 1
    print("Done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
