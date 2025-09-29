import json
import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draft_app.top4_routes import bp as top4_bp
from draft_app.transfer_system import init_transfers_for_league


@pytest.fixture
def isolated_top4_state(tmp_path, monkeypatch):
    """Prepare isolated state files for TOP4/UCL/EPL drafts."""
    # Ensure no S3 sync is attempted during tests
    monkeypatch.delenv("TOP4_S3_BUCKET", raising=False)
    monkeypatch.delenv("TOP4_S3_STATE_KEY", raising=False)
    monkeypatch.delenv("DRAFT_S3_BUCKET", raising=False)
    monkeypatch.delenv("DRAFT_S3_STATE_KEY", raising=False)
    monkeypatch.delenv("UCL_S3_STATE_KEY", raising=False)
    monkeypatch.delenv("EPL_STATE_S3_KEY", raising=False)

    import draft_app.top4_services as top4_services_module
    import draft_app.transfer_system as ts_module

    from draft_app.transfer_system import TransferSystem

    participants = ["Андрей", "Женя"]

    # Point state files to the temporary directory
    top4_state_path = tmp_path / "draft_state_top4.json"
    ucl_state_path = tmp_path / "draft_state_ucl.json"
    epl_state_path = tmp_path / "draft_state_epl.json"

    # Adjust Top-4 services to use the temporary paths and reduced participant list
    monkeypatch.setattr(top4_services_module, "STATE_FILE", top4_state_path, raising=False)
    monkeypatch.setattr(top4_services_module, "TOP4_USERS", participants, raising=False)

    def _create_transfer_system(draft_type: str):
        draft_type_upper = (draft_type or "").upper()
        mapping = {
            "TOP4": top4_state_path,
            "UCL": ucl_state_path,
            "EPL": epl_state_path,
        }
        if draft_type_upper not in mapping:
            raise ValueError(f"Unsupported draft type for test: {draft_type}")
        return TransferSystem(draft_type_upper, mapping[draft_type_upper])

    monkeypatch.setattr(ts_module, "create_transfer_system", _create_transfer_system)
    monkeypatch.setattr(ts_module, "get_transfer_system", _create_transfer_system)

    # Prepare isolated EPL/UCL states to ensure they remain untouched
    ucl_state_content = {"sentinel": "ucl"}
    epl_state_content = {"sentinel": "epl"}
    ucl_state_path.write_text(json.dumps(ucl_state_content, ensure_ascii=False), encoding="utf-8")
    epl_state_path.write_text(json.dumps(epl_state_content, ensure_ascii=False), encoding="utf-8")

    # Create minimal Top-4 state with Андрей owning player 101
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
    top4_state_path.write_text(json.dumps(initial_state, ensure_ascii=False), encoding="utf-8")

    return {
        "participants": participants,
        "top4_state_path": top4_state_path,
        "ucl_state_path": ucl_state_path,
        "epl_state_path": epl_state_path,
        "ucl_state_content": ucl_state_content,
        "epl_state_content": epl_state_content,
        "create_transfer_system": ts_module.create_transfer_system,
    }


def test_top4_transfer_flow_and_admin_revert(isolated_top4_state):
    data = isolated_top4_state
    participants = data["participants"]

    # Open transfer window for Top-4 draft
    opened = init_transfers_for_league(
        "top4",
        participants,
        transfers_per_manager=1,
        position_limits={"GK": 2, "DEF": 6, "MID": 6, "FWD": 4},
        max_from_club=1,
    )
    assert opened is True

    state_on_disk = json.loads(data["top4_state_path"].read_text(encoding="utf-8"))
    assert state_on_disk.get("transfer_window", {}).get("active"), state_on_disk.get("transfer_window")
    active_window = state_on_disk.get("transfers", {}).get("active_window")
    assert active_window, state_on_disk.get("transfers")
    assert active_window.get("managers_order") == participants
    assert active_window.get("transfer_phase") == "out"

    create_transfer_system = data["create_transfer_system"]
    transfer_system = create_transfer_system("top4")

    # Manager performs transfer out (accidental)
    state = transfer_system.load_state()
    assert state.get("transfer_window"), "Transfer window should exist"
    assert state["transfer_window"].get("active"), state["transfer_window"]
    state = transfer_system.transfer_player_out(state, participants[0], 101, current_gw=1)
    transfer_system.save_state(state)

    state_after_out = transfer_system.load_state()
    assert state_after_out["transfer_window"]["transfer_phase"] == "in"
    assert state_after_out["transfers"]["available_players"], "Player should appear in transfer-out pool"

    # Admin reverts the last transfer out via the new endpoint
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
    app.secret_key = "test"
    app.register_blueprint(top4_bp)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_name"] = "Admin"
            sess["godmode"] = True
        response = client.post(
            "/top4/return_transfer_out_player",
            headers={"Referer": "/top4"},
        )
        assert response.status_code == 302

    reverted_state = transfer_system.load_state()
    assert reverted_state["transfer_window"]["transfer_phase"] == "out"
    assert reverted_state["transfer_window"]["current_user"] == participants[0]
    assert not reverted_state["transfers"]["available_players"], "Transfer-out pool should be cleared"
    roster_ids = [player.get("playerId") for player in reverted_state["rosters"][participants[0]]]
    assert 101 in roster_ids
    assert all(record.get("action") != "transfer_out" for record in reverted_state["transfers"]["history"])

    # Ensure other drafts were not modified
    assert json.loads(data["ucl_state_path"].read_text(encoding="utf-8")) == data["ucl_state_content"]
    assert json.loads(data["epl_state_path"].read_text(encoding="utf-8")) == data["epl_state_content"]

    # Continue normal transfer flow after revert
    state = transfer_system.load_state()
    state = transfer_system.transfer_player_out(state, participants[0], 101, current_gw=1)
    transfer_system.save_state(state)

    state = transfer_system.load_state()
    state.setdefault("transfers", {}).setdefault("available_players", []).append(
        {
            "playerId": 202,
            "fullName": "Player 202",
            "clubName": "Club B",
            "position": "MID",
            "status": "transfer_out",
        }
    )
    transfer_system.save_state(state)

    state = transfer_system.load_state()
    state = transfer_system.transfer_player_in(state, participants[0], 202, current_gw=1)
    transfer_system.save_state(state)

    final_state = transfer_system.load_state()
    final_roster_ids = [player.get("playerId") for player in final_state["rosters"][participants[0]]]
    assert 202 in final_roster_ids
    assert 101 not in final_roster_ids
    assert final_state["transfer_window"]["transfer_phase"] == "out"
    assert final_state["transfer_window"]["current_user"] == participants[1]


def test_transfer_in_players_visible_with_position_and_club_filters(isolated_top4_state, monkeypatch):
    data = isolated_top4_state

    sample_players = [
        {
            "playerId": 1001,
            "fullName": "Romelu Lukaku",
            "shortName": "Lukaku",
            "clubName": "Roma",
            "position": "FWD",
            "league": "Serie A",
            "price": 9.5,
            "popularity": 80,
            "fp_last": 12.0,
        },
        {
            "playerId": 1002,
            "fullName": "Victor Osimhen",
            "shortName": "Osimhen",
            "clubName": "Napoli",
            "position": "FWD",
            "league": "Serie A",
            "price": 10.5,
            "popularity": 90,
            "fp_last": 15.0,
        },
        {
            "playerId": 1003,
            "fullName": "Kevin De Bruyne",
            "shortName": "KDB",
            "clubName": "Man City",
            "position": "MID",
            "league": "Premier League",
            "price": 11.5,
            "popularity": 95,
            "fp_last": 18.0,
        },
    ]

    monkeypatch.setattr("draft_app.top4_routes.load_players", lambda: sample_players)

    # Open transfer window with Женя going first
    init_transfers_for_league(
        "top4",
        ["Женя", "Андрей"],
        transfers_per_manager=1,
        position_limits={"GK": 2, "DEF": 6, "MID": 6, "FWD": 4},
        max_from_club=1,
    )

    transfer_system = data["create_transfer_system"]("top4")

    # Set up Женя roster and perform transfer out of Lukaku
    state = transfer_system.load_state()
    state.setdefault("rosters", {})["Женя"] = [
        {
            "playerId": 1001,
            "fullName": "Romelu Lukaku",
            "clubName": "Roma",
            "position": "FWD",
            "price": 9.5,
        },
        {
            "playerId": 1004,
            "fullName": "Some Midfielder",
            "clubName": "Roma",
            "position": "MID",
            "price": 6.0,
        },
    ]
    transfer_system.save_state(state)

    state = transfer_system.load_state()
    state = transfer_system.transfer_player_out(state, "Женя", 1001, current_gw=1)
    state.setdefault("transfers", {}).setdefault("available_players", []).append(
        {
            "playerId": 1002,
            "fullName": "Victor Osimhen",
            "clubName": "Napoli",
            "position": "FWD",
            "status": "transfer_out",
        }
    )
    transfer_system.save_state(state)

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(top4_bp)

    captured_context = {}

    def fake_render_template(template_name, **context):
        captured_context.clear()
        captured_context.update(context)
        return "ok"

    monkeypatch.setattr("draft_app.top4_routes.render_template", fake_render_template)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_name"] = "Женя"
        response = client.get("/top4?position=FWD&club=Napoli")
        assert response.status_code == 200
        assert captured_context.get("players"), "Expected players to be available after filtering"
        player_names = {p.get("fullName") for p in captured_context["players"]}
        assert "Victor Osimhen" in player_names
        assert "Romelu Lukaku" not in player_names
