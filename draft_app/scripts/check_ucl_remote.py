"""Probe UEFA popupstats endpoints with random delays.

Usage:
    python -m draft_app.scripts.check_ucl_remote --players 97746 97923

You can also tweak delays via environment variables:
    MIN_DELAY=2 MAX_DELAY=6 python -m draft_app.scripts.check_ucl_remote

This script prints per-request diagnostics so you can compare behaviour
between local runs and Heroku dynos.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from typing import Iterable, List

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://gaming.uefa.com/en/uclfantasy/",
    "Origin": "https://gaming.uefa.com",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

URL_TEMPLATE = "https://gaming.uefa.com/en/uclfantasy/services/feeds/popupstats/popupstats_80_{player_id}.json"
DEFAULT_PLAYERS: List[int] = [
    97746,
    97923,
    98078,
    103823,
    1900733,
    50327420,
    50327427,
    150562676,
    250000919,
    250002096,
]


def _rand_delay() -> float:
    try:
        min_delay = float(os.getenv("MIN_DELAY", "1"))
    except Exception:
        min_delay = 1.0
    try:
        max_delay = float(os.getenv("MAX_DELAY", "5"))
    except Exception:
        max_delay = 5.0
    if max_delay < min_delay:
        min_delay, max_delay = max_delay, min_delay
    return random.uniform(min_delay, max_delay)


def _fetch(player_id: int, timeout: float) -> int:
    url = URL_TEMPLATE.format(player_id=player_id)
    started = time.time()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        duration = time.time() - started
        resp.raise_for_status()
        size = len(resp.content)
        print(
            f"[probe] player={player_id} status={resp.status_code} "
            f"bytes={size} duration={duration:.2f}s",
            flush=True,
        )
        return 0
    except requests.exceptions.Timeout:
        duration = time.time() - started
        print(
            f"[probe] player={player_id} timeout after {duration:.2f}s",
            flush=True,
        )
        return 1
    except Exception as exc:
        duration = time.time() - started
        print(
            f"[probe] player={player_id} error={exc!r} duration={duration:.2f}s",
            flush=True,
        )
        return 2


def run(players: Iterable[int], timeout: float) -> int:
    failures = 0
    for idx, player_id in enumerate(players, 1):
        print(f"[probe] [{idx}] waiting before next request…", flush=True)
        delay = _rand_delay()
        time.sleep(delay)
        print(f"[probe] [{idx}] delay={delay:.2f}s player={player_id}", flush=True)
        failures += _fetch(int(player_id), timeout)
    print(f"[probe] completed players={idx if 'idx' in locals() else 0} failures={failures}", flush=True)
    return failures


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--players",
        nargs="*",
        type=int,
        default=None,
        help="player IDs to probe (default: sample of 10)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("PROBE_TIMEOUT", "15")),
        help="per-request timeout in seconds",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    players = args.players or DEFAULT_PLAYERS
    if not players:
        print("[probe] no players provided", flush=True)
        return 0
    print(
        f"[probe] starting; players={len(players)} timeout={args.timeout}s "
        f"min_delay={os.getenv('MIN_DELAY', '1')} max_delay={os.getenv('MAX_DELAY', '5')}",
        flush=True,
    )
    failures = run(players, args.timeout)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
