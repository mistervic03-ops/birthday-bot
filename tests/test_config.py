import pytest

from config import _channel_id_env


def test_channel_id_env_accepts_channel_ids(monkeypatch) -> None:
    monkeypatch.setenv("BIRTHDAY_CHANNEL_ID", "C012345ABC")
    assert _channel_id_env("BIRTHDAY_CHANNEL_ID") == "C012345ABC"

    monkeypatch.setenv("BIRTHDAY_CHANNEL_ID", "G012345ABC")
    assert _channel_id_env("BIRTHDAY_CHANNEL_ID") == "G012345ABC"


def test_channel_id_env_rejects_dm_id_and_channel_name(monkeypatch) -> None:
    for value in ("D012345ABC", "#birthdays"):
        monkeypatch.setenv("BIRTHDAY_CHANNEL_ID", value)
        with pytest.raises(RuntimeError, match="Invalid Slack channel ID"):
            _channel_id_env("BIRTHDAY_CHANNEL_ID")
