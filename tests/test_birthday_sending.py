from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import birthday


class FakeSlackClient:
    def __init__(self) -> None:
        self.messages = []

    async def users_info(self, *, user: str) -> dict:
        return {"user": {"id": user, "deleted": False, "is_bot": False}}

    async def chat_postMessage(self, *, channel: str, text: str) -> None:
        self.messages.append({"channel": channel, "text": text})


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def fake_lock(pool, slack_user_id, birthday_date):
    yield True


def test_friday_sends_saturday_and_sunday_birthdays(monkeypatch) -> None:
    fetched_targets = []
    recorded_posts = []

    async def fetch_birthdays_for_targets(pool, targets):
        fetched_targets.extend(targets)
        return [
            {"slack_user_id": "USAT", "birth_month": 6, "birth_day": 20, "receive_wishes": True},
            {"slack_user_id": "USUN", "birth_month": 6, "birth_day": 21, "receive_wishes": True},
        ]

    async def has_birthday_post(pool, slack_user_id, birthday_date):
        return False

    async def record_birthday_post(pool, slack_user_id, birthday_date):
        recorded_posts.append((slack_user_id, birthday_date))
        return True

    monkeypatch.setattr(birthday.db, "fetch_birthdays_for_targets", fetch_birthdays_for_targets)
    monkeypatch.setattr(birthday.db, "birthday_send_lock", fake_lock)
    monkeypatch.setattr(birthday.db, "has_birthday_post", has_birthday_post)
    monkeypatch.setattr(birthday.db, "record_birthday_post", record_birthday_post)

    client = FakeSlackClient()
    settings = SimpleNamespace(birthday_channel_id="CBIRTHDAY", timezone="Asia/Seoul")
    run(birthday.send_today_birthdays(pool=object(), client=client, settings=settings, today=date(2026, 6, 19)))

    channel_messages = [message["text"] for message in client.messages if message["channel"] == "CBIRTHDAY"]
    assert fetched_targets == [(6, 19), (6, 20), (6, 21)]
    assert channel_messages == [
        "🎂 이번 주 토요일은 <@USAT> 님의 생일이에요! 미리 축하 메시지 남겨주세요 🎉",
        "🎂 이번 주 일요일은 <@USUN> 님의 생일이에요! 미리 축하 메시지 남겨주세요 🎉",
    ]
    assert recorded_posts == [("USAT", date(2026, 6, 20)), ("USUN", date(2026, 6, 21))]


def test_non_friday_does_not_check_weekend_birthdays(monkeypatch) -> None:
    fetched_targets = []

    async def fetch_birthdays_for_targets(pool, targets):
        fetched_targets.extend(targets)
        return []

    monkeypatch.setattr(birthday.db, "fetch_birthdays_for_targets", fetch_birthdays_for_targets)

    client = FakeSlackClient()
    settings = SimpleNamespace(birthday_channel_id="CBIRTHDAY", timezone="Asia/Seoul")
    run(birthday.send_today_birthdays(pool=object(), client=client, settings=settings, today=date(2026, 6, 18)))

    assert fetched_targets == [(6, 18)]
    assert client.messages == []
