from draft_app.transfer_system import create_transfer_system


def _clear_env(monkeypatch, *names):
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_create_transfer_system_uses_ucl_specific_env(monkeypatch):
    env_names = (
        "DRAFT_S3_UCL_STATE_KEY",
        "UCL_S3_STATE_KEY",
        "DRAFT_S3_STATE_KEY",
        "UCL_STATE_S3_KEY",
    )
    _clear_env(monkeypatch, *env_names)

    ts = create_transfer_system("ucl")
    assert ts.s3_key == "prod/draft_state_ucl.json"

    monkeypatch.setenv("DRAFT_S3_UCL_STATE_KEY", "custom/ucl.json")
    ts = create_transfer_system("ucl")
    assert ts.s3_key == "custom/ucl.json"
    _clear_env(monkeypatch, "DRAFT_S3_UCL_STATE_KEY")

    monkeypatch.setenv("UCL_S3_STATE_KEY", "legacy/ucl.json")
    ts = create_transfer_system("ucl")
    assert ts.s3_key == "legacy/ucl.json"
    _clear_env(monkeypatch, "UCL_S3_STATE_KEY")

    monkeypatch.setenv("DRAFT_S3_STATE_KEY", "shared/ucl_state.json")
    ts = create_transfer_system("ucl")
    assert ts.s3_key == "shared/ucl_state.json"
    _clear_env(monkeypatch, "DRAFT_S3_STATE_KEY")

    monkeypatch.setenv("UCL_STATE_S3_KEY", "old/ucl.json")
    ts = create_transfer_system("ucl")
    assert ts.s3_key == "old/ucl.json"


def test_create_transfer_system_uses_epl_env(monkeypatch):
    env_names = (
        "DRAFT_S3_STATE_KEY",
        "EPL_S3_STATE_KEY",
        "EPL_STATE_S3_KEY",
    )
    _clear_env(monkeypatch, *env_names)

    ts = create_transfer_system("epl")
    assert ts.s3_key == "draft_state_epl.json"

    monkeypatch.setenv("DRAFT_S3_STATE_KEY", "shared/epl_state.json")
    ts = create_transfer_system("epl")
    assert ts.s3_key == "shared/epl_state.json"
    _clear_env(monkeypatch, "DRAFT_S3_STATE_KEY")

    monkeypatch.setenv("EPL_S3_STATE_KEY", "legacy/epl.json")
    ts = create_transfer_system("epl")
    assert ts.s3_key == "legacy/epl.json"
    _clear_env(monkeypatch, "EPL_S3_STATE_KEY")

    monkeypatch.setenv("EPL_STATE_S3_KEY", "old/epl.json")
    ts = create_transfer_system("epl")
    assert ts.s3_key == "old/epl.json"


def test_create_transfer_system_uses_top4_env(monkeypatch):
    env_names = (
        "TOP4_S3_STATE_KEY",
        "TOP4_STATE_S3_KEY",
    )
    _clear_env(monkeypatch, *env_names)

    ts = create_transfer_system("top4")
    assert ts.s3_key == "draft_state_top4.json"

    monkeypatch.setenv("TOP4_S3_STATE_KEY", "custom/top4.json")
    ts = create_transfer_system("top4")
    assert ts.s3_key == "custom/top4.json"
    _clear_env(monkeypatch, "TOP4_S3_STATE_KEY")

    monkeypatch.setenv("TOP4_STATE_S3_KEY", "old/top4.json")
    ts = create_transfer_system("top4")
    assert ts.s3_key == "old/top4.json"
