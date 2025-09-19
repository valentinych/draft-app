import json
import os
import random
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError

from .services import HEADERS_GENERIC, HTTP_SESSION

_DEBUG_ENABLED = (os.getenv("UCL_STATS_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}


def _debug(event: str, **data) -> None:
    if not _DEBUG_ENABLED:
        return
    payload = " ".join(f"{k}={v}" for k, v in data.items())
    message = f"[UCL:debug] {event}"
    if payload:
        message = f"{message} {payload}"
    print(message, flush=True)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(float(raw))
    except Exception:
        return default

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "ucl_stat"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

POPUP_DIR = BASE_DIR / "popupstats"

TTL = None  # TTL disabled: cached payloads stay until explicitly refreshed
FEED_TTL = None
PLAYERS_FEED_LOCAL = BASE_DIR / "players_80_en_1.json"
REQUEST_CONNECT_TIMEOUT = _env_float("UCL_STATS_CONNECT_TIMEOUT", 4.0)
REQUEST_READ_TIMEOUT = _env_float("UCL_STATS_READ_TIMEOUT", 15.0)
REQUEST_TIMEOUT = (REQUEST_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT)
WARMUP_TIMEOUT = _env_float("UCL_STATS_WARMUP_TIMEOUT", max(REQUEST_READ_TIMEOUT, 10.0))
REMOTE_ATTEMPTS = max(2, _env_int("UCL_STATS_ATTEMPTS", 3))
REMOTE_FAILURE_COOLDOWN = max(0.0, _env_float("UCL_STATS_REMOTE_COOLDOWN", 120.0))
RETRY_DELAY = _env_float("UCL_STATS_RETRY_DELAY", 0.6)
RETRY_BACKOFF = max(1.0, _env_float("UCL_STATS_RETRY_BACKOFF", 1.4))
RETRY_JITTER = max(0.0, _env_float("UCL_STATS_RETRY_JITTER", 1.2))

_REMOTE_FAILURE_AT: float = 0.0
_S3_CLIENT = None


def _stats_bucket() -> Optional[str]:
    for var in ("UCL_STATS_S3_BUCKET", "DRAFT_S3_BUCKET", "AWS_S3_BUCKET"):
        val = (os.getenv(var) or "").strip()
        if val:
            return val
    return None


def _stats_client():
    global _S3_CLIENT
    if _S3_CLIENT is not None:
        return _S3_CLIENT
    bucket = _stats_bucket()
    if not bucket:
        return None
    try:
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        _S3_CLIENT = boto3.client(
            "s3",
            region_name=region,
            config=boto3.session.Config(
                connect_timeout=5,
                read_timeout=8,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
    except Exception:
        _S3_CLIENT = None
    return _S3_CLIENT


def _stats_enabled() -> bool:
    return _stats_client() is not None


def _stats_get_json(key: str) -> Optional[Dict]:
    client = _stats_client()
    bucket = _stats_bucket()
    if not client or not bucket:
        return None
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            return None
        print(f"[UCL:S3] get {key} failed: {exc}")
        return None
    except Exception as exc:
        print(f"[UCL:S3] get {key} failed: {exc}")
        return None


def _stats_put_json(key: str, payload: Dict) -> None:
    client = _stats_client()
    bucket = _stats_bucket()
    if not client or not bucket:
        return
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
            CacheControl="max-age=10800, public",
        )
    except Exception as exc:
        print(f"[UCL:S3] put {key} failed: {exc}")


def _s3_key(player_id: int) -> str:
    prefix = _stats_prefix()
    return f"{prefix}/popupstats_80_{int(player_id)}.json"


def _s3_feed_key() -> str:
    prefix = _stats_prefix()
    return f"{prefix}/feed.json"


def _stats_prefix() -> str:
    env_override = os.getenv("UCL_STATS_S3_PREFIX") or os.getenv("UCL_S3_STATS_PREFIX")
    if env_override:
        prefix = env_override.strip().strip("/")
        if prefix:
            return prefix
    return "ucl"


def _load_local(player_id: int) -> Optional[Dict]:
    path = CACHE_DIR / f"{int(player_id)}.json"
    if not path.exists():
        _debug("local_cache_miss", player_id=int(player_id), path=path)
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        _debug("local_cache_hit", player_id=int(player_id), path=path)
        return payload
    except Exception as exc:
        _debug("local_cache_error", player_id=int(player_id), path=path, error=exc)
        return None


def _save_local(player_id: int, payload: Dict) -> None:
    target = CACHE_DIR / f"{int(player_id)}.json"
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="ucl_stat_", suffix=".json", dir=str(CACHE_DIR))
        os.close(fd)
        with open(tmp_name, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, target)
        size = None
        try:
            size = target.stat().st_size
        except Exception:
            pass
        _debug("local_cache_write", player_id=int(player_id), path=target, bytes=size)
    except Exception as exc:
        _debug("local_cache_write_error", player_id=int(player_id), path=target, error=exc)


def _load_s3(player_id: int) -> Optional[Dict]:
    if not _stats_enabled():
        _debug("s3_disabled", player_id=int(player_id))
        return None
    payload = _stats_get_json(_s3_key(player_id))
    if not isinstance(payload, dict):
        _debug("s3_cache_miss", player_id=int(player_id))
        return None
    if isinstance(payload.get("cached_at"), str) and "data" in payload:
        _debug("s3_cache_hit", player_id=int(player_id))
        return payload
    wrapped = {
        "cached_at": datetime.utcnow().isoformat(),
        "data": payload,
    }
    try:
        _stats_put_json(_s3_key(player_id), wrapped)
    except Exception:
        pass
    _debug("s3_cache_legacy_wrap", player_id=int(player_id))
    return wrapped


def _save_s3(player_id: int, payload: Dict) -> None:
    if not _stats_enabled():
        _debug("s3_disabled", player_id=int(player_id), action="save")
        return
    _stats_put_json(_s3_key(player_id), payload)
    _debug("s3_cache_write", player_id=int(player_id))


def _fresh(payload: Optional[Dict]) -> bool:
    return bool(payload)


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
        remaining = REMOTE_FAILURE_COOLDOWN - (time.time() - _REMOTE_FAILURE_AT)
        _debug("remote_skip_cooldown", url=url, seconds=max(int(remaining), 0))
        return None

    headers = _http_headers()
    query_variants = (
        {},
        {"params": {"_": str(int(time.time()))}},
    )
    warmup_done = False
    for idx in range(REMOTE_ATTEMPTS):
        variant = query_variants[idx % len(query_variants)]
        kwargs = {k: v for k, v in variant.items() if v}
        variant_label = "cachebuster" if kwargs else "default"
        attempt = idx + 1
        _debug("remote_attempt", url=url, attempt=attempt, variant=variant_label)
        try:
            resp = HTTP_SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()
            _REMOTE_FAILURE_AT = 0.0
            _debug(
                "remote_success",
                url=url,
                attempt=attempt,
                variant=variant_label,
                status=resp.status_code,
                content_length=resp.headers.get("Content-Length"),
            )
            return resp.json()
        except Exception as exc:
            _debug("remote_failure", url=url, attempt=attempt, variant=variant_label, error=exc)
            if not warmup_done:
                warmup_done = True
                _debug("warmup_begin", url=url)
                try:
                    warmup_resp = HTTP_SESSION.get(
                        "https://gaming.uefa.com/en/uclfantasy/",
                        headers=headers,
                        timeout=(REQUEST_CONNECT_TIMEOUT, max(WARMUP_TIMEOUT, REQUEST_READ_TIMEOUT)),
                    )
                    _debug("warmup_success", status=warmup_resp.status_code)
                except Exception as warm_exc:
                    _debug("warmup_failure", error=warm_exc)
            base_delay = RETRY_DELAY * (RETRY_BACKOFF ** idx)
            jitter = random.random() * RETRY_JITTER
            sleep_for = base_delay + jitter
            _debug("remote_retry_wait", url=url, attempt=attempt, seconds=round(sleep_for, 2))
            time.sleep(sleep_for)
            continue

    _REMOTE_FAILURE_AT = time.time()
    _debug("remote_exhausted", url=url)
    return None


def _fetch_remote_player(player_id: int) -> Optional[Dict]:
    url = f"https://gaming.uefa.com/en/uclfantasy/services/feeds/popupstats/popupstats_80_{int(player_id)}.json"
    return _fetch_remote(url)


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
                _save_local(player_id, cached)
                _save_s3(player_id, cached)
            else:
                if s3_payload:
                    cached = s3_payload
                    _save_local(player_id, cached)
    return (cached or {}).get("data", {})


def stats_bucket() -> Optional[str]:
    """Expose the S3 bucket used for cached stats uploads."""
    return _stats_bucket()


def stats_s3_key(player_id: int) -> str:
    """Return the S3 key for a player's popup stats payload."""
    return _s3_key(player_id)


def refresh_player_stats(player_id: int) -> Dict:
    """Force-refresh popup stats from the remote feed and mirror them to S3."""
    remote = _fetch_remote_player(player_id)
    if remote is not None:
        payload = {
            "cached_at": datetime.utcnow().isoformat(),
            "data": remote,
        }
        _save_local(player_id, payload)
        _save_s3(player_id, payload)
        return remote

    cached = _load_local(player_id)
    if _fresh(cached):
        return (cached or {}).get("data", {})

    s3_payload = _load_s3(player_id)
    if _fresh(s3_payload):
        _save_local(player_id, s3_payload)
        return (s3_payload or {}).get("data", {})

    return {}


def _feed_fresh(payload: Optional[Dict]) -> bool:
    return bool(payload)


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
    if not _stats_enabled():
        return None
    payload = _stats_get_json(_s3_feed_key())
    return payload if isinstance(payload, dict) else None


def _save_feed_s3(payload: Dict) -> None:
    if not _stats_enabled():
        return
    _stats_put_json(_s3_feed_key(), payload)


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
