"""Fetch UCL popupstats locally and upload them to S3 under the ``ucl/`` prefix."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable, List, Set

from draft_app.ucl_stats_store import PLAYERS_FEED_LOCAL, refresh_players_batch


def _extract_ids(raw) -> Set[int]:
    ids: Set[int] = set()
    if isinstance(raw, list):
        for item in raw:
            ids.update(_extract_ids(item))
    elif isinstance(raw, dict):
        if "playerList" in raw and isinstance(raw["playerList"], list):
            ids.update(_extract_ids(raw["playerList"]))
        pid = raw.get("playerId") or raw.get("id") or raw.get("pid")
        if pid is not None:
            try:
                ids.add(int(pid))
            except Exception:
                pass
        for key in ("data", "value", "players"):
            child = raw.get(key)
            if child:
                ids.update(_extract_ids(child))
    return ids


def load_player_ids(path: Path) -> List[int]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    ids = sorted(_extract_ids(payload))
    if not ids:
        raise RuntimeError(f"no player ids found in {path}")
    return ids


def run(players: Iterable[int], sleep: float) -> None:
    players = list(players)
    total = len(players)
    print(f"[fetch] players={total} sleep={sleep}", flush=True)
    if sleep > 0:
        for idx, pid in enumerate(players, 1):
            print(f"[fetch] chunk {idx}/{total} -> {pid}", flush=True)
            refresh_players_batch([pid])
            time.sleep(sleep)
    else:
        refresh_players_batch(players)
    print("[fetch] completed", flush=True)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=PLAYERS_FEED_LOCAL,
        help=f"path to JSON with players (default: {PLAYERS_FEED_LOCAL})",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="sleep seconds between individual uploads (default: batch mode)",
    )
    parser.add_argument(
        "--player",
        action="append",
        type=int,
        help="explicit player id to fetch (can be repeated)",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.player:
        players = sorted(set(int(pid) for pid in args.player))
    else:
        try:
            players = load_player_ids(args.source)
        except Exception as exc:
            print(f"[fetch] failed to load players: {exc}", flush=True)
            return 1
    run(players, max(0.0, args.sleep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
