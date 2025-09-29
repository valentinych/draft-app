import json
import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draft_app.ucl import bp as ucl_bp
from draft_app.transfer_system import init_transfers_for_league


@pytest.fixture
def isolated_ucl_state(tmp_path, monkeypatch):
    """Prepare isolated state files for UCL/EPL/TOP4 drafts."""
    # Ensure no S3 sync is attempted during tests
    monkeypatch.delenv("DRAFT_S3_BUCKET", raising=False)
    monkeypatch.delenv("DRAFT_S3_UCL_STATE_KEY", raising=False)
    monkeypatch.delenv("UCL_S3_STATE_KEY", raising=False)
    monkeypatch.delenv("DRAFT_S3_STATE_KEY", raising=False)

    import draft_app.ucl as ucl_module
    import draft_app.transfer_system as ts_module

    from draft_app.transfer_system import TransferSystem

    # Redirect base directories used by UCL module to temporary location
    monkeypatch.setattr(ucl_module, "BASE_DIR", tmp_path)

    # Point state files to the temporary directory
    ucl_state_path = tmp_path / "draft_state_ucl.json"
    epl_state_path = tmp_path / "draft_state_epl.json"
    top4_state_path = tmp_path / "draft_state_top4.json"

    monkeypatch.setattr(ucl_module, "UCL_STATE", ucl_state_path)
    monkeypatch.setattr(ucl_module, "UCL_PLAYERS", tmp_path / "players_80_en_1.json")

    def _create_transfer_system(draft_type: str):
        draft_type_upper = (draft_type or "").upper()
        mapping = {
            "UCL": ucl_state_path,
            "EPL": epl_state_path,
            "TOP4": top4_state_path,
        }
        if draft_type_upper not in mapping:
            raise ValueError(f"Unsupported draft type for test: {draft_type}")
        return TransferSystem(draft_type_upper, mapping[draft_type_upper])

    monkeypatch.setattr(ts_module, "create_transfer_system", _create_transfer_system)
    monkeypatch.setattr(ts_module, "get_transfer_system", _create_transfer_system)

    # Seed player list with drafted and undrafted options
    players_payload = [
        {
            "playerId": 101,
            "fullName": "Player 101",
            "clubName": "Club A",
            "position": "MID",
            "price": 10,
        },
        {
            "playerId": 202,
            "fullName": "Player 202",
            "clubName": "Club B",
            "position": "MID",
            "price": 8,
        },
    ]
    (tmp_path / "players_80_en_1.json").write_text(
        json.dumps(players_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    # Prepare isolated EPL/TOP4 states to ensure they remain untouched
    epl_state_content = {"sentinel": "epl"}
    top4_state_content = {"sentinel": "top4"}
    epl_state_path.write_text(json.dumps(epl_state_content, ensure_ascii=False), encoding="utf-8")
    top4_state_path.write_text(json.dumps(top4_state_content, ensure_ascii=False), encoding="utf-8")

    # Create minimal UCL state with Андрей owning player 101
    initial_state = {
        "rosters": {
            "Андрей": [
                {
                    "playerId": 101,
                    "fullName": "Player 101",
                    "clubName": "Club A",
                    "position": "MID",
                    "price": 10,
                }
            ],
            "Женя": [],
        },
        "transfers": {
            "history": [],
            "available_players": [],
            "active_window": None,
        },
    }
    ucl_state_path.write_text(json.dumps(initial_state, ensure_ascii=False), encoding="utf-8")

    return {
        "ucl_state_path": ucl_state_path,
        "epl_state_path": epl_state_path,
        "top4_state_path": top4_state_path,
        "epl_state_content": epl_state_content,
        "top4_state_content": top4_state_content,
        "create_transfer_system": ts_module.create_transfer_system,
    }


def test_ucl_transfer_flow_and_admin_revert(isolated_ucl_state):
    data = isolated_ucl_state
    participants = ["Андрей", "Женя"]

    # Open transfer window for UCL draft
    opened = init_transfers_for_league(
        "ucl",
        participants,
        transfers_per_manager=1,
        position_limits={"GK": 3, "DEF": 8, "MID": 9, "FWD": 5},
        max_from_club=1,
    )
    assert opened is True

    state_on_disk = json.loads(data["ucl_state_path"].read_text(encoding="utf-8"))
    assert state_on_disk.get("transfer_window", {}).get("active"), state_on_disk.get("transfer_window")
    active_window = state_on_disk.get("transfers", {}).get("active_window")
    assert active_window, state_on_disk.get("transfers")
    assert active_window.get("managers_order") == participants
    assert active_window.get("transfer_phase") == "out"

    create_transfer_system = data["create_transfer_system"]
    transfer_system = create_transfer_system("ucl")

    # Manager performs transfer out (accidental)
    state = transfer_system.load_state()
    assert state.get("transfer_window"), "Transfer window should exist"
    assert state["transfer_window"].get("active"), state["transfer_window"]
    state = transfer_system.transfer_player_out(state, "Андрей", 101, current_gw=1)
    transfer_system.save_state(state)

    state_after_out = transfer_system.load_state()
    assert state_after_out["transfer_window"]["transfer_phase"] == "in"
    assert state_after_out["transfers"]["available_players"], "Player should appear in transfer-out pool"

    # Admin reverts the last transfer out via the new endpoint
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(ucl_bp)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_name"] = "Admin"
            sess["godmode"] = True
        response = client.post(
            "/ucl/return_transfer_out_player",
            headers={"Referer": "/ucl"},
        )
        assert response.status_code == 302

    reverted_state = transfer_system.load_state()
    assert reverted_state["transfer_window"]["transfer_phase"] == "out"
    assert reverted_state["transfer_window"]["current_user"] == "Андрей"
    assert not reverted_state["transfers"]["available_players"], "Transfer-out pool should be cleared"
    roster_ids = [player.get("playerId") for player in reverted_state["rosters"]["Андрей"]]
    assert 101 in roster_ids
    assert all(record.get("action") != "transfer_out" for record in reverted_state["transfers"]["history"])

    # Ensure other drafts were not modified
    assert json.loads(data["epl_state_path"].read_text(encoding="utf-8")) == data["epl_state_content"]
    assert json.loads(data["top4_state_path"].read_text(encoding="utf-8")) == data["top4_state_content"]

    # Continue normal transfer flow after revert
    state = transfer_system.load_state()
    state = transfer_system.transfer_player_out(state, "Андрей", 101, current_gw=1)
    transfer_system.save_state(state)

    state = transfer_system.load_state()
    state = transfer_system.transfer_player_in(state, "Андрей", 202, current_gw=1)
    transfer_system.save_state(state)

    final_state = transfer_system.load_state()
    final_roster_ids = [player.get("playerId") for player in final_state["rosters"]["Андрей"]]
    assert 202 in final_roster_ids
    assert 101 not in final_roster_ids
    assert final_state["transfer_window"]["transfer_phase"] == "out"
    assert final_state["transfer_window"]["current_user"] == participants[1]
