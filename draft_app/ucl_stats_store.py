import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from .epl_services import _s3_bucket, _s3_enabled, _s3_get_json, _s3_put_json
from .services import HEADERS_GENERIC, HTTP_SESSION

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "ucl_stat"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

POPUP_DIR = BASE_DIR / "popupstats"

TTL = timedelta(hours=3)
FEED_TTL = timedelta(hours=6)
PLAYERS_FEED_LOCAL = BASE_DIR / "players_80_en_1.json"
REQUEST_TIMEOUT = 5  # seconds per HTTP attempt
REMOTE_FAILURE_COOLDOWN = 300  # seconds to skip remote fetches after failure

_REMOTE_FAILURE_AT: float = 0.0


def _s3_key(player_id: int) -> str:
    prefix = _stats_prefix()
    return f"{prefix}/{int(player_id)}.json"


def _s3_feed_key() -> str:
    prefix = _stats_prefix()
    return f"{prefix}/feed.json"


def _stats_prefix() -> str:
    env_override = os.getenv("UCL_STATS_S3_PREFIX") or os.getenv("UCL_S3_STATS_PREFIX")
    if env_override:
        prefix = env_override.strip().strip("/")
        if prefix:
            return prefix
    return "ucl_stats"


def _load_local(player_id: int) -> Optional[Dict]:
    path = CACHE_DIR / f"{int(player_id)}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_local(player_id: int, payload: Dict) -> None:
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="ucl_stat_", suffix=".json", dir=str(CACHE_DIR))
        os.close(fd)
        with open(tmp_name, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, CACHE_DIR / f"{int(player_id)}.json")
    except Exception:
        pass


def _load_s3(player_id: int) -> Optional[Dict]:
    if not _s3_enabled():
        return None
    bucket = _s3_bucket()
    if not bucket:
        return None
    key = _s3_key(player_id)
    payload = _s3_get_json(bucket, key)
    return payload if isinstance(payload, dict) else None


def _save_s3(player_id: int, payload: Dict) -> None:
    if not _s3_enabled():
        return
    bucket = _s3_bucket()
    if not bucket:
        return
    _s3_put_json(bucket, _s3_key(player_id), payload)


def _fresh(payload: Optional[Dict]) -> bool:
    if not payload:
        return False
    ts = payload.get("cached_at")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts)
    except Exception:
        return False
    return datetime.utcnow() - cached < TTL


def _http_headers() -> Dict[str, str]:
    headers = dict(HEADERS_GENERIC)
    headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    headers.setdefault("Accept", "application/json, text/plain, */*")
    headers.setdefault("Referer", "https://gaming.uefa.com/en/uclfantasy/")
    headers.setdefault("Origin", "https://gaming.uefa.com")
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    headers.setdefault("Cache-Control", "no-cache")
    headers.setdefault("Pragma", "no-cache")
    headers.setdefault("Accept-Encoding", "gzip, deflate, br")
    headers.setdefault("Connection", "keep-alive")
    headers.setdefault("X-Requested-With", "XMLHttpRequest")
    headers.setdefault("Sec-Fetch-Dest", "empty")
    headers.setdefault("Sec-Fetch-Mode", "cors")
    headers.setdefault("Sec-Fetch-Site", "same-origin")
    headers.setdefault("sec-ch-ua", '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"')
    headers.setdefault("sec-ch-ua-mobile", "?0")
    headers.setdefault("sec-ch-ua-platform", '"Windows"')
    return headers


def _fetch_remote(url: str) -> Optional[Dict]:
    global _REMOTE_FAILURE_AT

    if _REMOTE_FAILURE_AT and (time.time() - _REMOTE_FAILURE_AT) < REMOTE_FAILURE_COOLDOWN:
        return None

    headers = _http_headers()
    query_variants = (
        {},
        {"params": {"_": str(int(time.time()))}},
    )
    warmup_done = False
    for idx in range(2):
        variant = query_variants[idx % len(query_variants)]
        kwargs = {k: v for k, v in variant.items() if v}
        try:
            resp = HTTP_SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()
            _REMOTE_FAILURE_AT = 0.0
            return resp.json()
        except Exception:
            if not warmup_done:
                warmup_done = True
                try:
                    HTTP_SESSION.get(
                        "https://gaming.uefa.com/en/uclfantasy/",
                        headers=headers,
                        timeout=REQUEST_TIMEOUT,
                    )
                except Exception:
                    pass
            time.sleep(0.3)
            continue

    _REMOTE_FAILURE_AT = time.time()
    return None


def _fetch_remote_player(player_id: int) -> Optional[Dict]:
    url = f"https://gaming.uefa.com/en/uclfantasy/services/feeds/popupstats/popupstats_80_{int(player_id)}.json"
    return _fetch_remote(url)


def _load_local_popupstats(player_id: int) -> Optional[Dict]:
    paths = [
        POPUP_DIR / f"popupstats_80_{int(player_id)}.json",
        POPUP_DIR / f"popupstats_70_{int(player_id)}.json",
    ]
    for path in paths:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def _total_points(payload: Dict) -> int:
    try:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        value = data.get("value") if isinstance(data.get("value"), dict) else data

        lists = []
        for source in (value, data, payload):
            if isinstance(source, dict):
                for key in ("matchdayPoints", "points"):
                    raw = source.get(key)
                    if isinstance(raw, list):
                        lists.append(raw)

        total = 0
        for lst in lists:
            for entry in lst:
                if not isinstance(entry, dict):
                    continue
                val = entry.get("tPoints")
                if isinstance(val, (int, float)):
                    total += int(val)
                elif isinstance(val, str):
                    digits = "".join(ch for ch in val if (ch.isdigit() or ch in ".-"))
                    if digits and any(ch.isdigit() for ch in digits):
                        try:
                            total += int(float(digits))
                        except Exception:
                            continue
        return total
    except Exception:
        return 0


def get_player_stats(player_id: int) -> Dict:
    """Return cached popup stats for a player, updating if stale."""
    cached = _load_local(player_id)
    if not _fresh(cached):
        s3_payload = _load_s3(player_id)
        if _fresh(s3_payload):
            cached = s3_payload
            _save_local(player_id, cached)
        else:
            remote = _fetch_remote_player(player_id)
            if remote is not None:
                cached = {
                    "cached_at": datetime.utcnow().isoformat(),
                    "data": remote,
                }
                local_popup = _load_local_popupstats(player_id)
                if local_popup is not None:
                    remote_points = _total_points(remote)
                    fallback_points = _total_points(local_popup)
                    if fallback_points and remote_points == 0:
                        cached["data"] = local_popup
                _save_local(player_id, cached)
                _save_s3(player_id, cached)
            else:
                local_popup = _load_local_popupstats(player_id)
                if local_popup is not None:
                    cached = {
                        "cached_at": datetime.utcnow().isoformat(),
                        "data": local_popup,
                    }
                    _save_local(player_id, cached)
                    _save_s3(player_id, cached)
                elif s3_payload:
                    cached = s3_payload
                    _save_local(player_id, cached)
    return (cached or {}).get("data", {})


def _feed_fresh(payload: Optional[Dict]) -> bool:
    if not payload:
        return False
    ts = payload.get("cached_at")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts)
    except Exception:
        return False
    return datetime.utcnow() - cached < FEED_TTL


def _load_feed_local() -> Optional[Dict]:
    path = CACHE_DIR / "players_feed.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_feed_local(payload: Dict) -> None:
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="ucl_feed_", suffix=".json", dir=str(CACHE_DIR))
        os.close(fd)
        with open(tmp_name, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, CACHE_DIR / "players_feed.json")
    except Exception:
        pass


def _load_feed_s3() -> Optional[Dict]:
    if not _s3_enabled():
        return None
    bucket = _s3_bucket()
    if not bucket:
        return None
    payload = _s3_get_json(bucket, _s3_feed_key())
    return payload if isinstance(payload, dict) else None


def _save_feed_s3(payload: Dict) -> None:
    if not _s3_enabled():
        return
    bucket = _s3_bucket()
    if not bucket:
        return
    _s3_put_json(bucket, _s3_feed_key(), payload)


def _fetch_feed_remote() -> Optional[Dict]:
    url = "https://gaming.uefa.com/en/uclfantasy/services/feeds/players/players_80_en_1.json"
    return _fetch_remote(url)


def _load_local_players_backup() -> Optional[Dict]:
    if PLAYERS_FEED_LOCAL.exists():
        try:
            with PLAYERS_FEED_LOCAL.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def get_players_feed() -> Dict:
    cached = _load_feed_local()
    if not _feed_fresh(cached):
        s3_payload = _load_feed_s3()
        if _feed_fresh(s3_payload):
            cached = s3_payload
            _save_feed_local(cached)
        else:
            remote = _fetch_feed_remote()
            if remote is not None:
                cached = {
                    "cached_at": datetime.utcnow().isoformat(),
                    "data": remote,
                }
                _save_feed_local(cached)
                _save_feed_s3(cached)
            elif s3_payload:
                cached = s3_payload
                _save_feed_local(cached)
            else:
                fallback = _load_local_players_backup()
                if fallback is not None:
                    cached = {"cached_at": datetime.utcnow().isoformat(), "data": fallback}
                    _save_feed_local(cached)
    return (cached or {}).get("data", {})


def get_current_matchday() -> Optional[int]:
    feed = get_players_feed()
    player_list = []
    if isinstance(feed, dict):
        value = feed.get("data", {}).get("value") if isinstance(feed.get("data"), dict) else feed.get("value")
        if isinstance(value, dict):
            player_list = value.get("playerList") or []
    if not player_list and isinstance(feed, list):
        player_list = feed
    for player in player_list:
        matches = player.get("currentMatchesList") or player.get("upcomingMatchesList") or []
        for match in matches:
            md_raw = match.get("mdId")
            if md_raw is None:
                continue
            if isinstance(md_raw, int):
                return md_raw
            if isinstance(md_raw, str):
                digits = "".join(ch for ch in md_raw if ch.isdigit())
                if digits:
                    try:
                        return int(digits)
                    except Exception:
                        continue
    return None
