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

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
]

BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "Referer": "https://gaming.uefa.com/en/uclfantasy/",
    "Origin": "https://gaming.uefa.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
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


def _build_session(user_agent: str, cookie_string: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    session.headers["User-Agent"] = user_agent
    if cookie_string:
        cookie_pairs = [chunk.strip() for chunk in cookie_string.split(";") if chunk.strip()]
        for pair in cookie_pairs:
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            session.cookies.set(key.strip(), value.strip())
    return session


def _warmup(session: requests.Session, url: str, timeout: float) -> None:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        print(
            f"[probe] warmup url={url} status={resp.status_code} bytes={len(resp.content)}",
            flush=True,
        )
    except Exception as exc:
        print(f"[probe] warmup failed url={url} error={exc}", flush=True)


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


def _fetch(session: requests.Session, player_id: int, timeout: float) -> int:
    url = URL_TEMPLATE.format(player_id=player_id)
    started = time.time()
    try:
        resp = session.get(url, timeout=timeout)
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


def run(
    players: Iterable[int],
    timeout: float,
    cookie_string: str | None,
    warmup_url: str | None,
    per_request_warmup: bool,
) -> int:
    user_agent = random.choice(USER_AGENTS)
    session = _build_session(user_agent, cookie_string)
    if warmup_url and not per_request_warmup:
        _warmup(session, warmup_url, timeout)

    failures = 0
    for idx, player_id in enumerate(players, 1):
        print(f"[probe] [{idx}] waiting before next request…", flush=True)
        delay = _rand_delay()
        time.sleep(delay)
        print(f"[probe] [{idx}] delay={delay:.2f}s player={player_id}", flush=True)
        if warmup_url and per_request_warmup:
            _warmup(session, warmup_url, timeout)
        failures += _fetch(session, int(player_id), timeout)
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
    parser.add_argument(
        "--cookie",
        type=str,
        default=os.getenv("PROBE_COOKIE"),
        help="optional cookie string to attach to the session",
    )
    parser.add_argument(
        "--warmup-url",
        type=str,
        default=os.getenv("PROBE_WARMUP", "https://gaming.uefa.com/en/uclfantasy/"),
        help="URL to call before requests (set to '' to disable)",
    )
    parser.add_argument(
        "--per-request-warmup",
        action="store_true",
        help="call warmup URL before each player request (slower but closer to browser)",
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
        f"min_delay={os.getenv('MIN_DELAY', '1')} max_delay={os.getenv('MAX_DELAY', '5')} "
        f"user_agent=random warmup={'off' if not args.warmup_url else ('per-request' if args.per_request_warmup else 'once')}",
        flush=True,
    )
    warmup_url = args.warmup_url or None
    failures = run(
        players,
        args.timeout,
        args.cookie,
        warmup_url,
        args.per_request_warmup,
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
