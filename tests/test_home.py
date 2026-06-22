from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import home


def block_ids(view: dict) -> set[str]:
    return {block["block_id"] for block in view["blocks"] if "block_id" in block}


def view_text(view: dict) -> str:
    parts = []
    for block in view["blocks"]:
        text = block.get("text")
        if isinstance(text, dict):
            parts.append(text.get("text", ""))
        for field in block.get("fields", []):
            parts.append(field.get("text", ""))
        for element in block.get("elements", []):
            if isinstance(element, dict):
                text = element.get("text")
            else:
                text = None
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(text, dict):
                parts.append(text.get("text", ""))
    return "\n".join(parts)


def action_ids(view: dict) -> set[str]:
    ids = set()
    for block in view["blocks"]:
        for element in block.get("elements", []):
            action_id = element.get("action_id")
            if action_id:
                ids.add(action_id)
    return ids


def button_for_action(view: dict, action_id: str) -> dict:
    for block in view["blocks"]:
        for element in block.get("elements", []):
            if element.get("action_id") == action_id:
                return element
    raise AssertionError(f"missing action: {action_id}")


def test_registered_user_rendering() -> None:
    view = home.build_home_view(
        home.HomeData(
            birthday_record={"birth_month": 3, "birth_day": 15},
            receive_wishes=True,
            today_birthdays=[],
        )
    )

    text = view_text(view)
    assert "🎂 Bigxday" in text
    assert "*내 생일 정보*" in text
    assert "03월 15일" in text
    assert "받는 중" in text


def test_unregistered_user_rendering() -> None:
    view = home.build_home_view(
        home.HomeData(
            birthday_record=None,
            receive_wishes=True,
            today_birthdays=[],
        )
    )

    text = view_text(view)
    assert "생일 정보가 아직 등록되지 않았습니다." in text
    assert "HR 명부 동기화 후 자동 반영됩니다." in text
    assert "미등록" in text


def test_optout_button_rendering() -> None:
    view = home.build_home_view(
        home.HomeData(
            birthday_record={"birth_month": 3, "birth_day": 15},
            receive_wishes=True,
            today_birthdays=[],
        )
    )

    button = button_for_action(view, home.OPTOUT_ACTION_ID)
    assert button["text"]["text"] == "생일 공지 받지 않기"
    assert button["style"] == "danger"
    assert action_ids(view) == {home.OPTOUT_ACTION_ID}


def test_optin_button_rendering() -> None:
    view = home.build_home_view(
        home.HomeData(
            birthday_record={"birth_month": 3, "birth_day": 15},
            receive_wishes=False,
            today_birthdays=[],
        )
    )

    button = button_for_action(view, home.OPTIN_ACTION_ID)
    assert button["text"]["text"] == "생일 공지 다시 받기"
    assert button["style"] == "primary"
    assert action_ids(view) == {home.OPTIN_ACTION_ID}


def test_regular_user_does_not_see_admin_section() -> None:
    view = home.build_home_view(
        home.HomeData(
            birthday_record={"birth_month": 3, "birth_day": 15},
            receive_wishes=True,
            today_birthdays=[],
            is_admin=False,
        )
    )

    assert "bigxday_home_admin_summary" not in block_ids(view)
    assert "/birthday admin sync" not in view_text(view)


def test_admin_sees_admin_section() -> None:
    view = home.build_home_view(
        home.HomeData(
            birthday_record={"birth_month": 3, "birth_day": 15},
            receive_wishes=True,
            today_birthdays=[],
            is_admin=True,
            active_birthday_count=12,
            recent_log_summary="성공 2건, 실패 1건, 예약 0건",
        )
    )

    text = view_text(view)
    assert "bigxday_home_admin_summary" in block_ids(view)
    assert "활성 생일자 수: 12명" in text
    assert "최근 발송: 성공 2건, 실패 1건, 예약 0건" in text
    assert "/birthday admin sync" in text


def test_today_birthdays_empty_state() -> None:
    view = home.build_home_view(
        home.HomeData(
            birthday_record={"birth_month": 3, "birth_day": 15},
            receive_wishes=True,
            today_birthdays=[],
        )
    )

    text = view_text(view)
    assert "🎉 오늘 생일인 동료" in text
    assert "오늘 생일인 동료가 없습니다." in text


def test_today_birthdays_with_users() -> None:
    view = home.build_home_view(
        home.HomeData(
            birthday_record={"birth_month": 3, "birth_day": 15},
            receive_wishes=True,
            today_birthdays=[
                {"slack_user_id": "UUSER", "receive_wishes": True},
                {"slack_user_id": "UOTHER", "receive_wishes": True},
            ],
        )
    )

    text = view_text(view)
    assert "🎉 오늘 생일인 동료" in text
    assert "• <@UUSER>" in text
    assert "• <@UOTHER>" in text


def test_load_home_data_uses_only_today_targets(monkeypatch) -> None:
    calls = []

    async def get_receive_wishes(pool, user_id):
        return True

    async def fetch_active_birthday_for_user(pool, user_id):
        return {"birth_month": 6, "birth_day": 19}

    async def fetch_birthdays_for_targets(pool, targets):
        calls.append(targets)
        return [{"slack_user_id": "UUSER", "receive_wishes": True}]

    async def is_workspace_admin(user_id, settings):
        return False

    monkeypatch.setattr(home.db, "get_receive_wishes", get_receive_wishes)
    monkeypatch.setattr(home.db, "fetch_active_birthday_for_user", fetch_active_birthday_for_user)
    monkeypatch.setattr(home.db, "fetch_birthdays_for_targets", fetch_birthdays_for_targets)
    monkeypatch.setattr(home, "is_workspace_admin", is_workspace_admin)

    data = run_async(
        home.load_home_data(
            pool=object(),
            settings=SimpleNamespace(timezone="Asia/Seoul"),
            user_id="UUSER",
            today=date(2026, 6, 19),
        )
    )

    assert calls == [[(6, 19)]]
    assert data.today_birthdays == [{"slack_user_id": "UUSER", "receive_wishes": True}]


def test_update_receive_wishes_refreshes_home(monkeypatch) -> None:
    calls = []

    async def set_receive_wishes(pool, user_id, receive_wishes):
        calls.append(("set", pool, user_id, receive_wishes))

    async def publish_home(*, client, pool, settings, user_id):
        calls.append(("publish", client, pool, settings, user_id))

    client = object()
    pool = object()
    settings = object()
    monkeypatch.setattr(home.db, "set_receive_wishes", set_receive_wishes)
    monkeypatch.setattr(home, "publish_home", publish_home)

    run_async(
        home.update_receive_wishes_and_refresh(
            client=client,
            pool=pool,
            settings=settings,
            user_id="UUSER",
            receive_wishes=False,
        )
    )

    assert calls == [
        ("set", pool, "UUSER", False),
        ("publish", client, pool, settings, "UUSER"),
    ]


def run_async(coro):
    import asyncio

    return asyncio.run(coro)
