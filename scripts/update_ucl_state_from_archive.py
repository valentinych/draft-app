"""Update UCL draft state rosters using archived UEFA players feed."""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "draft_state_ucl.json"
ARCHIVE_PLAYERS_PATH = BASE_DIR / "players_80_en_1.json"
BACKUP_DIR = BASE_DIR / "data" / "backups" / "ucl"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_PATH = BACKUP_DIR / "draft_state_ucl_archive.json"

SKILL_TO_POSITION = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def _load_players() -> dict[int, dict[str, object]]:
    with ARCHIVE_PLAYERS_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    try:
        players = raw["data"]["value"]["playerList"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Unexpected structure of archive players file") from exc

    mapping: dict[int, dict[str, object]] = {}
    for entry in players:
        pid_raw = entry.get("id")
        try:
            pid = int(pid_raw)
        except (TypeError, ValueError):
            continue

        position = SKILL_TO_POSITION.get(int(entry.get("skill", 0)))
        if not position:
            # Skip records with unknown position encoding
            continue

        try:
            price = float(entry.get("value", 0))
        except (TypeError, ValueError):
            price = 0.0

        mapping[pid] = {
            "fullName": entry.get("pFName") or entry.get("latinName") or "",
            "clubName": entry.get("tName") or "",
            "position": position,
            "price": price,
        }
    return mapping


def _update_player_record(record: dict[str, object], lookup: dict[int, dict[str, object]]) -> bool:
    pid = record.get("playerId") or record.get("player_id") or record.get("playerID")
    if pid is None:
        return False
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False

    info = lookup.get(pid_int)
    if not info:
        return False

    if "fullName" in record:
        record["fullName"] = info["fullName"]
    if "player_name" in record:
        record["player_name"] = info["fullName"]

    if "clubName" in record:
        record["clubName"] = info["clubName"]
    if "club" in record:
        record["club"] = info["clubName"]

    if "position" in record:
        record["position"] = info["position"]
    if "pos" in record:
        record["pos"] = info["position"]

    if "price" in record:
        record["price"] = info["price"]

    return True


def main() -> None:
    players_lookup = _load_players()

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    # Backup current state for manual inspection if needed.
    BACKUP_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    updated_rosters = 0
    missing_rosters = 0
    for roster in state.get("rosters", {}).values():
        if not isinstance(roster, list):
            continue
        for player in roster:
            if _update_player_record(player, players_lookup):
                updated_rosters += 1
            else:
                missing_rosters += 1

    updated_picks = 0
    missing_picks = 0
    for pick in state.get("picks", []):
        if _update_player_record(pick, players_lookup):
            updated_picks += 1
        else:
            missing_picks += 1

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        "Updated rosters entries:", updated_rosters,
        "Missing roster entries:", missing_rosters,
        "Updated picks:", updated_picks,
        "Missing picks:", missing_picks,
    )


if __name__ == "__main__":
    main()
