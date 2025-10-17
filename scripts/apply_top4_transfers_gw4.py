#!/usr/bin/env python3
"""Apply post-GW4 Top-4 transfers and export S3 payloads.

This helper downloads the latest production Top-4 state via the
``/top4/lineups/data`` endpoint, applies the requested transfer list, and
materialises JSON files that can be uploaded to AWS S3.

Outputs are written under ``s3_exports/top4``:

* ``draft_state_top4.json`` – updated draft state (S3 key
  ``draft_state_top4.json``)
* ``lineups_round7.json`` – cached lineups payload with the embedded
  state replaced.  Upload to the ``top4_lineups`` prefix if the cache
  needs to be refreshed alongside the state.
* ``gw4_transfer_summary.json`` – machine-readable audit log of the
  applied transfers (purely informational).

The script is idempotent – running it multiple times will regenerate the
same files.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests

BASE_DIR = Path(__file__).resolve().parents[1]
EXPORT_DIR = BASE_DIR / "s3_exports" / "top4"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

LINEUPS_URL = "https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/lineups/data"
PLAYER_FILE = BASE_DIR / "data" / "cache" / "top4_players.json"


@dataclass
class TransferRequest:
    out_name: str
    in_name: str
    manager_hint: Optional[str] = None


TRANSFERS: List[TransferRequest] = [
    TransferRequest("Субельдия", "Сенеси"),
    TransferRequest("Опенда", "Илич", manager_hint="Ксана"),
    TransferRequest("Мингеса", "Руэда"),
    TransferRequest("Роджерс", "Грилиш"),
    TransferRequest("Мигель Гутьеррес", "Бремер"),
    TransferRequest("Серхио Гомес", "Франса"),
    TransferRequest("Айосе Перес", "Ансах"),
    TransferRequest("Соланке", "Изидор"),
    TransferRequest("Ундав", "Левелинг"),
    TransferRequest("Аит-Нури", "Гехи"),
    TransferRequest("Дэвид", "Ромуло"),
    TransferRequest("Вольтемаде", "Гнабри"),
    TransferRequest("Хак", "Форнальс"),
    TransferRequest("Лукаку", "Мурики"),
    TransferRequest("Порро", "Ромеро"),
    TransferRequest("Амири", "Гордон"),
    TransferRequest("Висса", "Аслани", manager_hint="Макс"),
    TransferRequest("Симеоне", "Альмада", manager_hint="Саша"),
    TransferRequest("Ливраменто", "Берн"),
]


def fetch_lineups_payload() -> Dict:
    """Fetch production lineups payload including the raw state."""

    for attempt in range(12):
        resp = requests.get(LINEUPS_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "processing":
            time.sleep(2.0)
            continue
        if "raw_state" not in data:
            raise RuntimeError("lineups payload missing raw_state")
        return data
    raise RuntimeError("timed out waiting for lineups cache to build")


def get_lineups_payload() -> Dict:
    override_path = os.environ.get("TOP4_LINEUPS_PAYLOAD")
    if override_path:
        path = Path(override_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if "raw_state" not in data:
            raise RuntimeError(
                f"override payload {path} missing raw_state"
            )
        return data
    return fetch_lineups_payload()


def load_players_index() -> Dict[str, Dict]:
    players = json.loads(PLAYER_FILE.read_text(encoding="utf-8"))
    index: Dict[str, Dict] = {}
    for player in players:
        name = player.get("fullName")
        if name and name not in index:
            index[name] = player
    return index


def locate_player(rosters: Dict[str, List[Dict]], name: str) -> Optional[tuple[str, int, Dict]]:
    for manager, roster in rosters.items():
        for idx, player in enumerate(roster or []):
            if player.get("fullName") == name:
                return manager, idx, player
    return None


def make_roster_entry(player_data: Dict) -> Dict:
    return {
        "playerId": player_data.get("playerId"),
        "fullName": player_data.get("fullName"),
        "clubName": player_data.get("clubName"),
        "position": player_data.get("position"),
        "league": player_data.get("league"),
    }


def apply_transfers(state: Dict, players_index: Dict[str, Dict]) -> List[Dict]:
    rosters = state.setdefault("rosters", {})
    summary: List[Dict] = []

    for request in TRANSFERS:
        out_name = request.out_name
        in_name = request.in_name

        found = locate_player(rosters, out_name)
        manager_name = request.manager_hint
        idx_to_replace: Optional[int] = None
        removed_player: Optional[Dict] = None

        if found:
            manager_name, idx_to_replace, removed_player = found
        elif manager_name is None:
            summary.append(
                {
                    "out": out_name,
                    "in": in_name,
                    "status": "out_player_not_found",
                    "details": "Исходный игрок отсутствует в составах",
                }
            )
            continue

        roster = rosters.get(manager_name)
        if roster is None:
            summary.append(
                {
                    "out": out_name,
                    "in": in_name,
                    "status": "manager_not_found",
                    "details": f"Не удалось определить менеджера для {out_name}",
                }
            )
            continue

        entry = players_index.get(in_name)
        if not entry:
            summary.append(
                {
                    "out": out_name,
                    "in": in_name,
                    "manager": manager_name,
                    "status": "new_player_missing",
                    "details": "Игрок не найден в top4_players.json",
                }
            )
            continue

        # Remove existing occurrence of the incoming player anywhere in the league
        existing_owner = locate_player(rosters, in_name)
        if existing_owner:
            owner_name, owner_idx, _ = existing_owner
            if owner_name != manager_name:
                rosters[owner_name].pop(owner_idx)
                summary.append(
                    {
                        "out": in_name,
                        "in": None,
                        "manager": owner_name,
                        "status": "incoming_player_released",
                        "details": f"{in_name} освобождён из состава {owner_name}",
                    }
                )
                # Adjust index if we're removing from the same manager before insertion
                if owner_name == manager_name and idx_to_replace is not None and owner_idx < idx_to_replace:
                    idx_to_replace -= 1

        # Remove the outgoing player from the target roster if present
        if idx_to_replace is not None and removed_player is not None:
            roster.pop(idx_to_replace)
        else:
            # Ensure the outgoing player doesn't linger elsewhere
            orphan = locate_player(rosters, out_name)
            if orphan:
                rosters[orphan[0]].pop(orphan[1])

        # Check if the incoming player is already present after the cleanup
        already_idx = None
        for idx, player in enumerate(roster):
            if player.get("fullName") == in_name:
                already_idx = idx
                break

        new_entry = make_roster_entry(entry)

        if already_idx is not None:
            roster[already_idx] = new_entry
            status = "updated_existing"
            idx_used = already_idx
        else:
            insert_at = idx_to_replace if idx_to_replace is not None else len(roster)
            roster.insert(insert_at, new_entry)
            status = "inserted"
            idx_used = insert_at

        summary.append(
            {
                "out": out_name,
                "in": in_name,
                "manager": manager_name,
                "status": status,
                "roster_index": idx_used,
            }
        )

    return summary


def main() -> int:
    try:
        payload = get_lineups_payload()
    except Exception as exc:  # pragma: no cover - network failure is reported to CLI
        print(f"[error] Не удалось получить данные lineups: {exc}", file=sys.stderr)
        return 1

    raw_state = payload.get("raw_state")
    if not isinstance(raw_state, dict):
        print("[error] Некорректный формат raw_state", file=sys.stderr)
        return 1

    players_index = load_players_index()
    summary = apply_transfers(raw_state, players_index)

    # Persist updated state
    state_path = EXPORT_DIR / "draft_state_top4.json"
    state_path.write_text(json.dumps(raw_state, ensure_ascii=False, indent=2), encoding="utf-8")

    # Persist adjusted lineups payload with new raw_state embedded
    payload["raw_state"] = raw_state
    payload_path = EXPORT_DIR / "lineups_round7.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write audit log for convenience
    summary_path = EXPORT_DIR / "gw4_transfer_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Экспорт завершён. Обновлённое состояние: {state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
