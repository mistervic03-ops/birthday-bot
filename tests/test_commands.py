from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import commands


class FakeSlackClient:
    def __init__(self) -> None:
        self.messages = []
        self.users = {
            "UADMIN": {
                "id": "UADMIN",
                "name": "admin",
                "is_admin": True,
                "profile": {"real_name": "관리자"},
            },
            "UOWNER": {
                "id": "UOWNER",
                "name": "owner",
                "is_owner": True,
                "profile": {"real_name": "오너"},
            },
            "UUSER": {
                "id": "UUSER",
                "name": "hong",
                "profile": {"real_name": "홍길동", "display_name": "Gildong"},
            },
            "UOTHER": {
                "id": "UOTHER",
                "name": "other",
                "real_name": "RealOther",
                "profile": {"real_name": "김영희", "display_name": "OtherDisplay"},
            },
            "UNORMAL": {
                "id": "UNORMAL",
                "name": "normal",
                "profile": {"real_name": "일반유저"},
            },
        }

    async def users_info(self, *, user: str) -> dict:
        return {"user": self.users[user]}

    async def chat_postMessage(self, *, channel: str, text: str) -> None:
        self.messages.append({"channel": channel, "text": text})

    async def users_list(self, **kwargs) -> dict:
        return {"members": list(self.users.values()), "response_metadata": {"next_cursor": ""}}


class FakeSlackApiError(Exception):
    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.response = {"error": error}


class FailingPostSlackClient(FakeSlackClient):
    async def chat_postMessage(self, *, channel: str, text: str) -> None:
        raise FakeSlackApiError("not_in_channel")


class FailingAdminLookupSlackClient(FakeSlackClient):
    async def users_info(self, *, user: str) -> dict:
        raise RuntimeError("slack unavailable")


class InactiveSlackClient(FakeSlackClient):
    async def users_info(self, *, user: str) -> dict:
        result = await super().users_info(user=user)
        result["user"]["deleted"] = True
        return result


class CountingAdminLookupSlackClient(FakeSlackClient):
    def __init__(self) -> None:
        super().__init__()
        self.users_info_calls = []

    async def users_info(self, *, user: str) -> dict:
        self.users_info_calls.append(user)
        return await super().users_info(user=user)


def run(coro):
    return asyncio.run(coro)


def make_responder():
    responses = []

    async def respond(**kwargs):
        responses.append(kwargs)

    return responses, respond


def processing_error_response() -> dict:
    return {"text": commands.PROCESSING_ERROR_MESSAGE, "response_type": "ephemeral"}


def test_regular_help_hides_admin_commands() -> None:
    commands._slack_client = FakeSlackClient()
    responses, respond = make_responder()
    for text in ("", "help", "unknown"):
        run(
            commands.route_birthday_command(
                pool=object(),
                settings=object(),
                command={"user_id": "UNORMAL", "text": text},
                respond=respond,
            )
        )

    assert responses == [
        {"text": commands.BIRTHDAY_USER_HELP_MESSAGE, "response_type": "ephemeral"},
        {"text": commands.BIRTHDAY_USER_HELP_MESSAGE, "response_type": "ephemeral"},
        {"text": commands.BIRTHDAY_USER_HELP_MESSAGE, "response_type": "ephemeral"},
    ]
    assert "/birthday admin sync" not in commands.BIRTHDAY_USER_HELP_MESSAGE


def test_admin_help_shows_admin_commands() -> None:
    commands._slack_client = FakeSlackClient()
    responses, respond = make_responder()

    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "help"},
            respond=respond,
        )
    )
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin help"},
            respond=respond,
        )
    )

    assert responses == [
        {"text": commands.BIRTHDAY_ADMIN_HELP_MESSAGE, "response_type": "ephemeral"},
        {"text": commands.BIRTHDAY_ADMIN_HELP_MESSAGE, "response_type": "ephemeral"},
    ]
    assert "/birthday admin sync" in commands.BIRTHDAY_ADMIN_HELP_MESSAGE


def test_admin_help_rejects_regular_user() -> None:
    commands._slack_client = FakeSlackClient()
    responses, respond = make_responder()

    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UNORMAL", "text": "admin help"},
            respond=respond,
        )
    )

    assert responses == [{"text": "이 커맨드는 관리자만 사용할 수 있어요 🙏", "response_type": "ephemeral"}]


def test_admin_list_and_log_render_success(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()

    async def fetch_active_birthdays(pool):
        return [
            {"slack_user_id": "UUSER", "birth_month": 3, "birth_day": 15, "email": "u@example.com"},
            {"slack_user_id": "UOTHER", "birth_month": 4, "birth_day": 2, "email": "o@example.com"},
        ]

    async def fetch_recent_birthday_posts(pool, limit):
        return [
            {
                "slack_user_id": "UUSER",
                "birthday_date": date(2026, 6, 17),
                "posted_at": datetime(2026, 6, 17, 9, 0),
                "email": "u@example.com",
            }
        ]

    monkeypatch.setattr(commands.db, "fetch_active_birthdays", fetch_active_birthdays)
    monkeypatch.setattr(commands.db, "fetch_recent_birthday_posts", fetch_recent_birthday_posts)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin list"},
            respond=respond,
        )
    )
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin log"},
            respond=respond,
        )
    )

    assert responses[0] == {
        "text": "03-15 홍길동 (<@UUSER>)\n04-02 김영희 (<@UOTHER>)",
        "response_type": "ephemeral",
    }
    assert responses[1] == {
        "text": "2026-06-17 홍길동 — 발송완료",
        "response_type": "ephemeral",
    }


def test_admin_sync_returns_counts() -> None:
    commands._slack_client = FakeSlackClient()

    async def sync_runner(**kwargs):
        return SimpleNamespace(upserted=7, deactivated=2)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UOWNER", "text": "admin sync"},
            respond=respond,
            sync_runner=sync_runner,
        )
    )

    assert responses == [
        {
            "text": "HR 시트 동기화 완료: 7명 upsert, 2명 비활성화",
            "response_type": "ephemeral",
        }
    ]


def test_admin_sync_error_returns_ephemeral() -> None:
    commands._slack_client = FakeSlackClient()

    async def sync_runner(**kwargs):
        raise RuntimeError("sync failed")

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UOWNER", "text": "admin sync"},
            respond=respond,
            sync_runner=sync_runner,
        )
    )

    assert responses == [
        {
            "text": "동기화 중 오류가 발생했어요. 로그를 확인해주세요.",
            "response_type": "ephemeral",
        }
    ]


def test_admin_rejects_non_admin_before_work(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()
    called = False

    async def fetch_active_birthdays(pool):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(commands.db, "fetch_active_birthdays", fetch_active_birthdays)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UNORMAL", "text": "admin list"},
            respond=respond,
        )
    )

    assert called is False
    assert responses == [{"text": "이 커맨드는 관리자만 사용할 수 있어요 🙏", "response_type": "ephemeral"}]


def test_admin_check_fails_closed() -> None:
    commands._slack_client = FailingAdminLookupSlackClient()

    assert run(commands.is_workspace_admin("UADMIN")) is False


def test_admin_user_ids_grants_admin_without_slack_api() -> None:
    commands._slack_client = FailingAdminLookupSlackClient()
    settings = SimpleNamespace(admin_user_ids=["UALLOW"])

    assert run(commands.is_workspace_admin("UALLOW", settings)) is True


def test_admin_user_ids_miss_falls_back_to_slack_api() -> None:
    client = CountingAdminLookupSlackClient()
    commands._slack_client = client
    settings = SimpleNamespace(admin_user_ids=["UALLOW"])

    assert run(commands.is_workspace_admin("UADMIN", settings)) is True
    assert client.users_info_calls == ["UADMIN"]


def test_status_includes_birthday_registration(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()
    birthday_rows = {
        "UUSER": {"slack_user_id": "UUSER", "birth_month": 3, "birth_day": 15, "email": None},
        "UOTHER": None,
        "UNORMAL": None,
    }

    async def get_receive_wishes(pool, slack_user_id):
        return slack_user_id != "UNORMAL"

    async def fetch_active_birthday_for_user(pool, slack_user_id):
        return birthday_rows[slack_user_id]

    monkeypatch.setattr(commands.db, "get_receive_wishes", get_receive_wishes)
    monkeypatch.setattr(commands.db, "fetch_active_birthday_for_user", fetch_active_birthday_for_user)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UUSER", "text": "status"},
            respond=respond,
        )
    )
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UOTHER", "text": "status"},
            respond=respond,
        )
    )
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UNORMAL", "text": "status"},
            respond=respond,
        )
    )

    assert responses == [
        {"text": "현재 생일 공지를 받고 계세요 🎂\n생일 등록됨 (03-15)", "response_type": "ephemeral"},
        {"text": "현재 생일 공지를 받고 계세요 🎂\n생일 미등록", "response_type": "ephemeral"},
        {"text": "현재 생일 공지를 받지 않고 있어요\n생일 미등록", "response_type": "ephemeral"},
    ]


def test_opt_commands_return_ephemeral_on_db_error(monkeypatch) -> None:
    async def set_receive_wishes(pool, slack_user_id, receive_wishes):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(commands.db, "set_receive_wishes", set_receive_wishes)

    responses, respond = make_responder()
    for text in ("optout", "optin"):
        run(
            commands.route_birthday_command(
                pool=object(),
                settings=object(),
                command={"user_id": "UUSER", "text": text},
                respond=respond,
            )
        )

    assert responses == [processing_error_response(), processing_error_response()]


def test_status_returns_ephemeral_on_db_error(monkeypatch) -> None:
    async def get_receive_wishes(pool, slack_user_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(commands.db, "get_receive_wishes", get_receive_wishes)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UUSER", "text": "status"},
            respond=respond,
        )
    )

    assert responses == [processing_error_response()]


def test_admin_set_upserts_birthday(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()
    upsert_calls = []

    async def upsert_birthday(pool, *, slack_user_id, birth_month, birth_day, email, source):
        upsert_calls.append(
            {
                "slack_user_id": slack_user_id,
                "birth_month": birth_month,
                "birth_day": birth_day,
                "email": email,
                "source": source,
            }
        )

    monkeypatch.setattr(commands.db, "upsert_birthday", upsert_birthday)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin set <@UUSER> 03-15"},
            respond=respond,
        )
    )

    assert upsert_calls == [
        {
            "slack_user_id": "UUSER",
            "birth_month": 3,
            "birth_day": 15,
            "email": None,
            "source": "manual",
        }
    ]
    assert responses == [
        {
            "text": "<@UUSER> 님의 생일을 03-15로 등록했습니다.",
            "response_type": "ephemeral",
        }
    ]


def test_admin_list_and_log_return_ephemeral_on_db_error(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()

    async def fetch_active_birthdays(pool):
        raise RuntimeError("database unavailable")

    async def fetch_recent_birthday_posts(pool, limit):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(commands.db, "fetch_active_birthdays", fetch_active_birthdays)
    monkeypatch.setattr(commands.db, "fetch_recent_birthday_posts", fetch_recent_birthday_posts)

    responses, respond = make_responder()
    for text in ("admin list", "admin log"):
        run(
            commands.route_birthday_command(
                pool=object(),
                settings=object(),
                command={"user_id": "UADMIN", "text": text},
                respond=respond,
            )
        )

    assert responses == [processing_error_response(), processing_error_response()]


def test_admin_set_returns_ephemeral_on_db_error(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()

    async def upsert_birthday(pool, *, slack_user_id, birth_month, birth_day, email, source):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(commands.db, "upsert_birthday", upsert_birthday)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin set <@UUSER> 03-15"},
            respond=respond,
        )
    )

    assert responses == [processing_error_response()]


def test_admin_set_accepts_labeled_mention_and_case_insensitive_username(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()
    upsert_calls = []

    async def upsert_birthday(pool, *, slack_user_id, birth_month, birth_day, email, source):
        upsert_calls.append((slack_user_id, birth_month, birth_day, email, source))

    monkeypatch.setattr(commands.db, "upsert_birthday", upsert_birthday)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin set <@UUSER|홍길동> 03-15"},
            respond=respond,
        )
    )
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin set @realother 04-02"},
            respond=respond,
        )
    )

    assert upsert_calls == [
        ("UUSER", 3, 15, None, "manual"),
        ("UOTHER", 4, 2, None, "manual"),
    ]
    assert responses == [
        {
            "text": "<@UUSER> 님의 생일을 03-15로 등록했습니다.",
            "response_type": "ephemeral",
        },
        {
            "text": "<@UOTHER> 님의 생일을 04-02로 등록했습니다.",
            "response_type": "ephemeral",
        },
    ]


def test_admin_reset_onboarding_runs_reset(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()
    calls = []

    async def reset_onboarding(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(commands, "reset_onboarding", reset_onboarding)

    settings = object()
    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool="pool",
            settings=settings,
            command={"user_id": "UADMIN", "text": "admin reset-onboarding"},
            respond=respond,
        )
    )

    assert calls == [{"pool": "pool", "client": commands._slack_client, "settings": settings}]
    assert responses == [
        {
            "text": "온보딩 메시지를 초기화하고 재발송했습니다.",
            "response_type": "ephemeral",
        }
    ]


def test_admin_reset_onboarding_failure_returns_ephemeral(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()

    async def reset_onboarding(**kwargs):
        return False

    monkeypatch.setattr(commands, "reset_onboarding", reset_onboarding)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool="pool",
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin reset-onboarding"},
            respond=respond,
        )
    )

    assert responses == [
        {"text": "온보딩 메시지 발송에 실패했어요.", "response_type": "ephemeral"}
    ]


def test_admin_test_birthday_sends_without_duplicate_check(monkeypatch) -> None:
    client = FakeSlackClient()
    commands._slack_client = client

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("birthday_posts duplicate path should be skipped")

    monkeypatch.setattr(commands.db, "has_birthday_post", fail_if_called)
    monkeypatch.setattr(commands.db, "record_birthday_post", fail_if_called)

    settings = SimpleNamespace(birthday_channel_id="CBIRTHDAY")
    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=settings,
            command={"user_id": "UADMIN", "text": "admin test-birthday <@UUSER>"},
            respond=respond,
        )
    )

    assert client.messages == [
        {
            "channel": "CBIRTHDAY",
            "text": "🎂 오늘은 <@UUSER> 님의 생일이에요! 다 같이 축하해드려요 🎉",
        },
        {
            "channel": "UUSER",
            "text": "🎂 <@UUSER> 님, 생일 축하드려요! 오늘 하루 행복하게 보내세요 ☀️",
        },
    ]
    assert responses == [{"text": "테스트 발송 완료: <@UUSER>", "response_type": "ephemeral"}]


def test_admin_test_birthday_send_failure_returns_reason() -> None:
    commands._slack_client = FailingPostSlackClient()

    settings = SimpleNamespace(birthday_channel_id="CBIRTHDAY")
    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=settings,
            command={"user_id": "UADMIN", "text": "admin test-birthday <@UUSER>"},
            respond=respond,
        )
    )

    assert responses == [
        {"text": "발송 실패: not_in_channel — 봇이 채널에 없어요.", "response_type": "ephemeral"}
    ]


def test_admin_preview_lists_targets_without_sending(monkeypatch) -> None:
    client = FakeSlackClient()
    commands._slack_client = client

    async def fetch_birthdays_for_targets(pool, targets):
        assert targets == [(6, 19), (6, 20), (6, 21)]
        return [
            {
                "slack_user_id": "UUSER",
                "birth_month": 6,
                "birth_day": 19,
                "email": "user@example.com",
                "receive_wishes": True,
            }
        ]

    async def has_birthday_post(pool, slack_user_id, birthday_date):
        return False

    monkeypatch.setattr(commands.db, "fetch_birthdays_for_targets", fetch_birthdays_for_targets)
    monkeypatch.setattr(commands.db, "has_birthday_post", has_birthday_post)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin preview 2026-06-19"},
            respond=respond,
        )
    )

    assert client.messages == []
    assert responses == [
        {
            "text": "2026-06-19 preview\n- 2026-06-19 홍길동 (<@UUSER>): 발송 예정",
            "response_type": "ephemeral",
        }
    ]


def test_admin_preview_marks_inactive_slack_users_without_sending(monkeypatch) -> None:
    client = InactiveSlackClient()
    commands._slack_client = client

    async def fetch_birthdays_for_targets(pool, targets):
        return [
            {
                "slack_user_id": "UUSER",
                "birth_month": 6,
                "birth_day": 19,
                "email": "user@example.com",
                "receive_wishes": True,
            }
        ]

    async def has_birthday_post(pool, slack_user_id, birthday_date):
        return False

    monkeypatch.setattr(commands.db, "fetch_birthdays_for_targets", fetch_birthdays_for_targets)
    monkeypatch.setattr(commands.db, "has_birthday_post", has_birthday_post)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UADMIN", "text": "admin preview 2026-06-19"},
            respond=respond,
        )
    )

    assert client.messages == []
    assert responses == [
        {
            "text": "2026-06-19 preview\n- 2026-06-19 홍길동 (<@UUSER>): 스킵: 비활성 Slack 유저",
            "response_type": "ephemeral",
        }
    ]


def test_admin_test_weekend_sends_saturday_message_without_duplicate_check(monkeypatch) -> None:
    client = FakeSlackClient()
    commands._slack_client = client

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("birthday_posts duplicate path should be skipped")

    monkeypatch.setattr(commands.db, "has_birthday_post", fail_if_called)
    monkeypatch.setattr(commands.db, "record_birthday_post", fail_if_called)

    settings = SimpleNamespace(birthday_channel_id="CBIRTHDAY")
    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=settings,
            command={"user_id": "UADMIN", "text": "admin test-weekend <@UUSER>"},
            respond=respond,
        )
    )

    assert client.messages == [
        {
            "channel": "CBIRTHDAY",
            "text": "🎂 이번 주 토요일은 <@UUSER> 님의 생일이에요! 미리 축하 메시지 남겨주세요 🎉",
        },
        {
            "channel": "UUSER",
            "text": "🎂 <@UUSER> 님, 이번 주 토요일이 생일이시네요! 미리 축하드려요 🎉",
        },
    ]
    assert responses == [{"text": "주말 테스트 발송 완료: <@UUSER>", "response_type": "ephemeral"}]


def test_admin_test_weekend_send_failure_returns_reason() -> None:
    commands._slack_client = FailingPostSlackClient()

    settings = SimpleNamespace(birthday_channel_id="CBIRTHDAY")
    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=settings,
            command={"user_id": "UADMIN", "text": "admin test-weekend <@UUSER>"},
            respond=respond,
        )
    )

    assert responses == [
        {"text": "발송 실패: not_in_channel — 봇이 채널에 없어요.", "response_type": "ephemeral"}
    ]


def test_new_admin_test_commands_reject_non_admin(monkeypatch) -> None:
    commands._slack_client = FakeSlackClient()
    called = False

    async def send_test_birthday(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(commands, "send_test_birthday", send_test_birthday)

    responses, respond = make_responder()
    run(
        commands.route_birthday_command(
            pool=object(),
            settings=object(),
            command={"user_id": "UNORMAL", "text": "admin test-birthday <@UUSER>"},
            respond=respond,
        )
    )

    assert called is False
    assert responses == [{"text": "이 커맨드는 관리자만 사용할 수 있어요 🙏", "response_type": "ephemeral"}]
