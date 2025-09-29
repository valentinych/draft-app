from draft_app import ucl


def test_ensure_ucl_state_shape_repairs_corrupted_rosters(monkeypatch):
    # Prepare fake players feed for lookup during rebuild
    feed_players = [
        {"playerId": 101, "fullName": "Alice", "clubName": "Club A", "position": "MID", "price": 7.5},
        {"playerId": 102, "fullName": "Bob", "clubName": "Club B", "position": "FWD", "price": 8.0},
        {"playerId": 999, "fullName": "Legacy", "clubName": "Legacy FC", "position": "DEF", "price": 4.0},
    ]

    def fake_json_load(path):
        if path == ucl.UCL_PLAYERS:
            return feed_players
        return {}

    saved_state = {}

    def fake_state_save(state):
        saved_state.update(state)

    monkeypatch.setattr(ucl, "_json_load", fake_json_load)
    monkeypatch.setattr(ucl, "_ucl_state_save", fake_state_save)

    state = {
        "rosters": {
            "Ксана": [
                {"playerId": 101, "custom": "keep"},
                "broken-entry",
                {"playerId": 999, "note": "transfer"},
            ],
            "Андрей": None,
        },
        "picks": [
            {
                "round": 1,
                "user": "Ксана",
                "playerId": 101,
                "player_name": "Alice",
                "club": "Club A",
                "pos": "MID",
            },
            {
                "round": 1,
                "user": "Андрей",
                "playerId": 102,
                "player_name": "Bob",
                "club": "Club B",
                "pos": "FWD",
            },
        ],
        "draft_order": [],
        "current_pick_index": 0,
    }

    result = ucl._ensure_ucl_state_shape(state)

    # Roster for Ксана keeps existing metadata and order restored from picks
    roster_ksana = result["rosters"]["Ксана"]
    assert [p["playerId"] for p in roster_ksana] == [101, 999]
    assert roster_ksana[0]["fullName"] == "Alice"
    assert roster_ksana[0]["clubName"] == "Club A"
    assert roster_ksana[0]["position"] == "MID"
    assert roster_ksana[0]["custom"] == "keep"

    # Legacy player without pick is preserved
    assert roster_ksana[1]["playerId"] == 999
    assert roster_ksana[1]["note"] == "transfer"

    # Андрей roster rebuilt from pick
    roster_andrey = result["rosters"]["Андрей"]
    assert [p["playerId"] for p in roster_andrey] == [102]
    assert roster_andrey[0]["fullName"] == "Bob"
    assert roster_andrey[0]["clubName"] == "Club B"
    assert roster_andrey[0]["position"] == "FWD"

    # Ensure save was triggered after modification
    assert "rosters" in saved_state
