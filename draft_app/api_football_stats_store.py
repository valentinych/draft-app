"""
API Football statistics store for Top-4 draft
Fetches player statistics from API Football and converts them to Top-4 format
"""
from __future__ import annotations
import os
import json
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from .api_football_client import api_football_client
from .api_football_score_converter import convert_api_football_stats_to_top4_format
from .top4_services import (
    _s3_enabled,
    _s3_bucket,
    _s3_get_json,
    _s3_put_json,
    TOP4_CACHE_VERSION,
)
from .player_map_store import load_player_map

BASE_DIR = Path(__file__).resolve().parent.parent
API_FOOTBALL_STATS_DIR = BASE_DIR / "data" / "cache" / "api_football_stats" / TOP4_CACHE_VERSION
API_FOOTBALL_STATS_DIR.mkdir(parents=True, exist_ok=True)

STATS_CACHE_TTL = timedelta(hours=24)  # Cache for 24 hours


def _s3_prefix() -> str:
    """Return S3 prefix for cached API Football stats"""
    base = os.getenv("TOP4_S3_API_FOOTBALL_STATS_PREFIX", "api_football_stats")
    return f"{base.rstrip('/')}/{TOP4_CACHE_VERSION}"


def _s3_key(pid: int) -> str:
    prefix = _s3_prefix().strip().strip("/")
    return f"{prefix}/{int(pid)}.json"


def _fresh(data: Dict, force_refresh: bool = False) -> bool:
    """Check if cached data is fresh"""
    if force_refresh:
        return False
    ts = data.get("cached_at")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts)
        return datetime.utcnow() - cached < STATS_CACHE_TTL
    except Exception:
        return False


def load_api_football_stats(pid: int, force_refresh: bool = False) -> Dict:
    """Load cached API Football stats for a player"""
    p = API_FOOTBALL_STATS_DIR / f"{int(pid)}.json"
    data = None
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if _fresh(data, force_refresh):
                return data
        except Exception:
            data = None
    
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(pid)
        if bucket and key:
            s3_data = _s3_get_json(bucket, key)
            if isinstance(s3_data, dict) and _fresh(s3_data, force_refresh):
                tmp_fd, tmp_name = tempfile.mkstemp(prefix="api_football_", suffix=".json", dir=str(API_FOOTBALL_STATS_DIR))
                os.close(tmp_fd)
                with open(tmp_name, "w", encoding="utf-8") as f:
                    json.dump(s3_data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_name, p)
                return s3_data
    
    return data or {} if not force_refresh else {}


def save_api_football_stats(pid: int, data: Dict) -> None:
    """Save API Football stats to cache"""
    payload = dict(data or {})
    payload["cached_at"] = datetime.utcnow().isoformat()
    
    if _s3_enabled():
        bucket = _s3_bucket()
        key = _s3_key(pid)
        if bucket and key and not _s3_put_json(bucket, key, payload):
            print(f"[API_FOOTBALL:S3] save_api_football_stats fallback pid={pid}")
    
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="api_football_", suffix=".json", dir=str(API_FOOTBALL_STATS_DIR))
    os.close(tmp_fd)
    with open(tmp_name, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_name, API_FOOTBALL_STATS_DIR / f"{int(pid)}.json")


def fetch_player_stats_from_api_football(api_football_id: int, league_id: int, season: int = 2025) -> Optional[Dict]:
    """Fetch player statistics from API Football for a specific league"""
    try:
        stats = api_football_client.get_player_statistics(api_football_id, league_id, season)
        if stats:
            return stats
    except Exception as e:
        print(f"[API_FOOTBALL] Error fetching stats for player {api_football_id}: {e}")
    return None


def get_player_round_stats_from_api_football(
    api_football_id: int,
    league_id: int,
    round_no: int,
    season: int = 2025
) -> Optional[Dict]:
    """
    Get player statistics for a specific round from API Football
    
    Note: API Football provides season statistics, not per-round.
    For per-round stats, we need to fetch fixtures and calculate.
    This is a simplified version that uses season stats.
    """
    # Load cached stats
    cached = load_api_football_stats(api_football_id)
    if cached and not cached.get("force_refresh"):
        # Check if we have round-specific data
        round_stats = cached.get("round_stats", [])
        for stat in round_stats:
            if stat.get("round") == round_no:
                return stat
    
    # Fetch from API
    stats_data = fetch_player_stats_from_api_football(api_football_id, league_id, season)
    if not stats_data:
        return None
    
    # For now, return season stats (per-round stats require fixture analysis)
    # This should be enhanced to fetch fixture-specific stats
    formatted_stats = {
        "round": round_no,
        "season_stats": stats_data,
        "cached_at": datetime.utcnow().isoformat(),
    }
    
    # Save to cache
    save_api_football_stats(api_football_id, {"round_stats": [formatted_stats]})
    
    return formatted_stats

