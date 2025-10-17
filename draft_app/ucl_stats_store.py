import json
import os
import random
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Optional
import subprocess
import shlex

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
WARMUP_URL = (os.getenv("UCL_STATS_WARMUP_URL") or "https://gaming.uefa.com/en/uclfantasy/").strip()
WARMUP_PER_REQUEST = (os.getenv("UCL_STATS_WARMUP_PER_REQUEST") or "").strip().lower() in {"1", "true", "yes", "on"}
COOKIE_STRING = (os.getenv("UCL_STATS_COOKIE") or "").strip()
USER_AGENT = (os.getenv("UCL_STATS_USER_AGENT") or "").strip()
USE_CURL = (os.getenv("UCL_STATS_USE_CURL") or "").strip().lower() in {"1", "true", "yes", "on"}
CURL_BIN = (os.getenv("UCL_STATS_CURL_BIN") or "curl").strip()
CURL_TIMEOUT = _env_float("UCL_STATS_CURL_TIMEOUT", REQUEST_READ_TIMEOUT)
CURL_EXTRA_ARGS = [
    chunk
    for chunk in shlex.split(os.getenv("UCL_STATS_CURL_ARGS", ""))
    if chunk
]

_REMOTE_FAILURE_AT: float = 0.0
_S3_CLIENT = None
_SESSION_PREPARED: bool = False
_SESSION_WARMED: bool = False


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
    if USER_AGENT:
        headers["User-Agent"] = USER_AGENT
    else:
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
    if COOKIE_STRING:
        headers.setdefault("Cookie", COOKIE_STRING)
    return headers


def _prepare_session() -> None:
    global _SESSION_PREPARED
    if _SESSION_PREPARED:
        return
    sess = HTTP_SESSION
    sess.headers.update(_http_headers())
    if COOKIE_STRING:
        cookie_pairs = [chunk.strip() for chunk in COOKIE_STRING.split(";") if chunk.strip()]
        for pair in cookie_pairs:
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            sess.cookies.set(key.strip(), value.strip())
    _SESSION_PREPARED = True


def _warmup_session(force: bool = False) -> None:
    global _SESSION_WARMED
    if not force and _SESSION_WARMED:
        return
    if not WARMUP_URL:
        _SESSION_WARMED = True
        return
    try:
        resp = HTTP_SESSION.get(WARMUP_URL, timeout=WARMUP_TIMEOUT, allow_redirects=True)
        print(
            f"[ucl:warmup] url={WARMUP_URL} status={resp.status_code} bytes={len(resp.content)}",
            flush=True,
        )
    except Exception as exc:
        print(f"[ucl:warmup] failed url={WARMUP_URL} error={exc}", flush=True)
    finally:
        _SESSION_WARMED = True


def _curl_headers() -> Dict[str, str]:
    return _http_headers()


def _run_curl(url: str, label: str) -> Optional[str]:
    headers = _curl_headers()
    cmd = [
        CURL_BIN,
        "-fsSL",
        "--max-time",
        str(CURL_TIMEOUT),
        "--connect-timeout",
        str(REQUEST_CONNECT_TIMEOUT),
        "--compressed",
    ]
    for key, value in headers.items():
        cmd.extend(["-H", f"{key}: {value}"])
    if CURL_EXTRA_ARGS:
        cmd.extend(CURL_EXTRA_ARGS)
    cmd.append(url)
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except Exception as exc:
        print(f"[ucl:curl] {label} command error url={url} error={exc}", flush=True)
        return None
    if result.returncode != 0:
        print(
            f"[ucl:curl] {label} nonzero exit={result.returncode} url={url} stderr={result.stderr.strip()}",
            flush=True,
        )
        return None
    print(
        f"[ucl:curl] {label} ok url={url} bytes={len(result.stdout)}",
        flush=True,
    )
    return result.stdout


def _curl_warmup(force: bool = False) -> None:
    global _SESSION_WARMED
    if not force and _SESSION_WARMED:
        return
    if not WARMUP_URL:
        _SESSION_WARMED = True
        return
    _run_curl(WARMUP_URL, "warmup")
    _SESSION_WARMED = True


def _cachebuster_url(url: str) -> str:
    token = str(int(time.time()))
    return f"{url}{'&' if '?' in url else '?'}_={token}"


def _fetch_remote_curl(url: str) -> Optional[Dict]:
    global _REMOTE_FAILURE_AT
    if _REMOTE_FAILURE_AT and (time.time() - _REMOTE_FAILURE_AT) < REMOTE_FAILURE_COOLDOWN:
        remaining = REMOTE_FAILURE_COOLDOWN - (time.time() - _REMOTE_FAILURE_AT)
        print(f"[ucl:fetch] skip due to cooldown url={url} remaining={round(max(remaining,0),2)}s", flush=True)
        return None

    if WARMUP_PER_REQUEST:
        _curl_warmup(force=True)
    else:
        _curl_warmup()

    for idx in range(REMOTE_ATTEMPTS):
        variant_label = "cachebuster" if idx % 2 == 1 else "default"
        variant_url = _cachebuster_url(url) if variant_label == "cachebuster" else url
        attempt = idx + 1
        print(f"[ucl:fetch] attempt={attempt} variant={variant_label} url={variant_url}", flush=True)
        stdout = _run_curl(variant_url, f"attempt={attempt}")
        if stdout is None:
            base_delay = RETRY_DELAY * (RETRY_BACKOFF ** idx)
            jitter = random.random() * RETRY_JITTER
            sleep_for = base_delay + jitter
            print(
                f"[ucl:fetch] retry in {round(sleep_for,2)}s attempt={attempt} url={url}",
                flush=True,
            )
            time.sleep(sleep_for)
            continue
        try:
            payload = json.loads(stdout)
            _REMOTE_FAILURE_AT = 0.0
            return payload
        except Exception as exc:
            print(
                f"[ucl:fetch] parse error attempt={attempt} url={variant_url} error={exc}",
                flush=True,
            )
            base_delay = RETRY_DELAY * (RETRY_BACKOFF ** idx)
            jitter = random.random() * RETRY_JITTER
            sleep_for = base_delay + jitter
            time.sleep(sleep_for)
            continue

    _REMOTE_FAILURE_AT = time.time()
    print(f"[ucl:fetch] exhausted attempts url={url}", flush=True)
    return None


def _fetch_remote(url: str) -> Optional[Dict]:
    global _REMOTE_FAILURE_AT

    if USE_CURL:
        return _fetch_remote_curl(url)

    _prepare_session()
    if WARMUP_PER_REQUEST:
        _warmup_session(force=True)
    else:
        _warmup_session()

    if _REMOTE_FAILURE_AT and (time.time() - _REMOTE_FAILURE_AT) < REMOTE_FAILURE_COOLDOWN:
        remaining = REMOTE_FAILURE_COOLDOWN - (time.time() - _REMOTE_FAILURE_AT)
        _debug("remote_skip_cooldown", url=url, seconds=max(int(remaining), 0))
        print(f"[ucl:fetch] skip due to cooldown url={url} remaining={round(max(remaining,0),2)}s", flush=True)
        return None

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
        print(f"[ucl:fetch] attempt={attempt} variant={variant_label} url={url}", flush=True)
        try:
            resp = HTTP_SESSION.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
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
            print(
                f"[ucl:fetch] success attempt={attempt} variant={variant_label} url={url} status={resp.status_code} len={resp.headers.get('Content-Length')}",
                flush=True,
            )
            return resp.json()
        except Exception as exc:
            _debug("remote_failure", url=url, attempt=attempt, variant=variant_label, error=exc)
            print(
                f"[ucl:fetch] failure attempt={attempt} variant={variant_label} url={url} error={exc}",
                flush=True,
            )
            if not warmup_done and not WARMUP_PER_REQUEST:
                warmup_done = True
                _debug("warmup_begin", url=url)
                try:
                    warmup_resp = HTTP_SESSION.get(
                        WARMUP_URL,
                        timeout=(REQUEST_CONNECT_TIMEOUT, max(WARMUP_TIMEOUT, REQUEST_READ_TIMEOUT)),
                        allow_redirects=True,
                    )
                    _debug("warmup_success", status=warmup_resp.status_code)
                    print(
                        f"[ucl:fetch] warmup url={WARMUP_URL} status={warmup_resp.status_code}",
                        flush=True,
                    )
                except Exception as warm_exc:
                    _debug("warmup_failure", error=warm_exc)
                    print(f"[ucl:fetch] warmup failure error={warm_exc}", flush=True)
            base_delay = RETRY_DELAY * (RETRY_BACKOFF ** idx)
            jitter = random.random() * RETRY_JITTER
            sleep_for = base_delay + jitter
            _debug("remote_retry_wait", url=url, attempt=attempt, seconds=round(sleep_for, 2))
            print(
                f"[ucl:fetch] retry in {round(sleep_for,2)}s attempt={attempt} url={url}",
                flush=True,
            )
            time.sleep(sleep_for)
            continue

    _REMOTE_FAILURE_AT = time.time()
    _debug("remote_exhausted", url=url)
    print(f"[ucl:fetch] exhausted attempts url={url}", flush=True)
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
        print(f"[ucl:refresh] player={player_id} fetched remote and saved", flush=True)
        return remote

    cached = _load_local(player_id)
    if _fresh(cached):
        print(f"[ucl:refresh] player={player_id} using local cache", flush=True)
        return (cached or {}).get("data", {})

    s3_payload = _load_s3(player_id)
    if _fresh(s3_payload):
        _save_local(player_id, s3_payload)
        print(f"[ucl:refresh] player={player_id} restored from S3 cache", flush=True)
        return (s3_payload or {}).get("data", {})

    print(f"[ucl:refresh] player={player_id} no data available", flush=True)
    return {}


def _describe_cache_state(path: Path, before_mtime: Optional[float]) -> tuple[str, Optional[int]]:
    if not path.exists():
        return ("missing", None)
    stat = path.stat()
    if before_mtime is None:
        state = "written"
    elif stat.st_mtime > (before_mtime + 1e-6):
        state = "updated"
    else:
        state = "unchanged"
    return (state, stat.st_size)


def refresh_players_batch(player_ids: Iterable[int]) -> Dict[str, object]:
    """Refresh popup stats for multiple players and mirror them to S3."""

    bucket = stats_bucket()
    results = []
    failures = 0

    ids_list = list(player_ids)
    print(f"[ucl:refresh] start batch players={len(ids_list)} bucket={bucket}", flush=True)

    for raw_pid in ids_list:
        try:
            pid = int(raw_pid)
        except Exception:
            print(f"[ucl:refresh] skip invalid player id={raw_pid}", flush=True)
            continue

        cache_path = CACHE_DIR / f"{pid}.json"
        try:
            before_mtime = cache_path.stat().st_mtime
        except Exception:
            before_mtime = None

        record: Dict[str, object] = {
            "player_id": pid,
            "cache_state": "missing",
            "cache_size": None,
            "points_entries": None,
            "name": "",
            "s3_key": stats_s3_key(pid),
            "error": None,
            "exception": None,
        }

        try:
            stats = refresh_player_stats(pid)
            cache_state, cache_size = _describe_cache_state(cache_path, before_mtime)
            record["cache_state"] = cache_state
            record["cache_size"] = cache_size

            if not stats:
                record["error"] = "empty"
                failures += 1
                print(
                    f"[ucl:refresh] player={pid} empty stats cache={cache_state} size={cache_size}",
                    flush=True,
                )
            else:
                value = stats.get("value") if isinstance(stats, dict) else None
                if value is None and isinstance(stats, dict):
                    data = stats.get("data") if isinstance(stats.get("data"), dict) else {}
                    value = data.get("value") if isinstance(data.get("value"), dict) else None
                if not isinstance(value, dict):
                    record["error"] = "missing_value"
                    failures += 1
                    print(
                        f"[ucl:refresh] player={pid} value missing cache={cache_state} size={cache_size}",
                        flush=True,
                    )
                else:
                    points = value.get("points") or value.get("matchdayPoints") or []
                    if isinstance(points, list):
                        record["points_entries"] = len(points)
                    display_name = (
                        value.get("shortName")
                        or value.get("fullName")
                        or value.get("name")
                        or ""
                    )
                    record["name"] = display_name
                    print(
                        f"[ucl:refresh] player={pid} ok name={display_name!r} points_entries={record['points_entries']} cache={cache_state} size={cache_size}",
                        flush=True,
                    )
        except Exception as exc:
            cache_state, cache_size = _describe_cache_state(cache_path, before_mtime)
            record["cache_state"] = cache_state
            record["cache_size"] = cache_size
            record["error"] = "exception"
            record["exception"] = repr(exc)
            failures += 1
            print(
                f"[ucl:refresh] player={pid} exception={exc} cache={cache_state} size={cache_size}",
                flush=True,
            )

        results.append(record)

    total = len(results)
    print(f"[ucl:refresh] done batch total={total} failures={failures}", flush=True)
    return {
        "bucket": bucket,
        "total": total,
        "failures": failures,
        "results": results,
    }


def get_player_stats_cached(player_id: int) -> Dict:
    """Return locally cached popup stats without performing remote requests."""

    cached = _load_local(player_id)
    if _fresh(cached):
        return (cached or {}).get("data", {})
    s3_payload = _load_s3(player_id)
    if _fresh(s3_payload):
        _save_local(player_id, s3_payload)
        return (s3_payload or {}).get("data", {})
    return {}


def get_current_matchday_cached() -> Optional[int]:
    """Return matchday derived from locally cached feeds only."""

    feed = _load_feed_local()
    if not feed:
        s3_feed = _load_feed_s3()
        if s3_feed:
            _save_feed_local(s3_feed)
            feed = s3_feed
    if not feed:
        return None
    payload = feed.get("data") if isinstance(feed, dict) else None
    value = payload.get("value") if isinstance(payload, dict) else None
    players = []
    if isinstance(value, dict):
        raw = value.get("playerList") or []
        if isinstance(raw, list):
            players = raw
    if not players and isinstance(payload, dict):
        alt = payload.get("playerList")
        if isinstance(alt, list):
            players = alt
    if not players and isinstance(feed, dict):
        alt = feed.get("playerList")
        if isinstance(alt, list):
            players = alt
    for player in players:
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
