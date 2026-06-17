from __future__ import annotations

import calendar
import re
from collections.abc import Awaitable, Callable
from typing import Any

import db

_slack_client: Any | None = None


SyncRunner = Callable[..., Awaitable[Any]]


async def is_workspace_admin(user_id: str) -> bool:
    if _slack_client is None:
        return False

    try:
        result = await _slack_client.users_info(user=user_id)
    except Exception:
        return False

    user = result.get("user") or {}
    return bool(user.get("is_admin") or user.get("is_owner"))


def register_commands(
    app: Any,
    pool: Any,
    settings: Any | None = None,
    sync_runner: SyncRunner | None = None,
) -> None:
    global _slack_client
    _slack_client = app.client

    @app.command("/birthday")
    async def handle_birthday_command(ack, command, respond):
        await ack()
        await route_birthday_command(
            pool=pool,
            settings=settings,
            command=command,
            respond=respond,
            sync_runner=sync_runner,
        )


async def route_birthday_command(
    *,
    pool: Any,
    settings: Any | None,
    command: dict[str, Any],
    respond: Callable[..., Awaitable[Any]],
    sync_runner: SyncRunner | None = None,
) -> None:
    slack_user_id = command["user_id"]
    raw_text = (command.get("text") or "").strip()
    text = raw_text.lower()
    parts = text.split()

    if parts and parts[0] == "admin":
        await handle_admin_command(
            pool=pool,
            settings=settings,
            command=command,
            respond=respond,
            sync_runner=sync_runner,
        )
        return

    if text == "optout":
        await db.set_receive_wishes(pool, slack_user_id, False)
        await respond(
            text="내 생일 채널 공지를 꺼뒀어요.",
            response_type="ephemeral",
        )
        return

    if text == "optin":
        await db.set_receive_wishes(pool, slack_user_id, True)
        await respond(
            text="내 생일 채널 공지를 다시 켰어요.",
            response_type="ephemeral",
        )
        return

    if text == "status":
        receive_wishes = await db.get_receive_wishes(pool, slack_user_id)
        birthday_record = await db.fetch_active_birthday_for_user(pool, slack_user_id)
        status = "켜짐" if receive_wishes else "꺼짐"
        birthday_status = format_birthday_status(birthday_record)
        await respond(
            text=f"내 생일 채널 공지: {status}\n{birthday_status}",
            response_type="ephemeral",
        )
        return

    await respond(
        text="사용 가능한 명령어: `/birthday optout`, `/birthday optin`, `/birthday status`",
        response_type="ephemeral",
    )


async def handle_admin_command(
    *,
    pool: Any,
    settings: Any | None,
    command: dict[str, Any],
    respond: Callable[..., Awaitable[Any]],
    sync_runner: SyncRunner | None = None,
) -> None:
    slack_user_id = command["user_id"]
    if not await is_workspace_admin(slack_user_id):
        await respond(text="관리자만 사용할 수 있는 명령어예요.", response_type="ephemeral")
        return

    raw_parts = (command.get("text") or "").strip().split()
    parts = [part.lower() for part in raw_parts]
    subcommand = parts[1] if len(parts) > 1 else ""

    if subcommand == "list":
        rows = await db.fetch_active_birthdays(pool)
        lines = [
            f"{row['birth_month']:02d}-{row['birth_day']:02d} "
            f"{await slack_display_name(row['slack_user_id'], record_get(row, 'email'))} "
            f"(<@{row['slack_user_id']}>)"
            for row in rows
        ]
        await respond(
            text="\n".join(lines) if lines else "활성 생일자가 없어요.",
            response_type="ephemeral",
        )
        return

    if subcommand == "log":
        rows = await db.fetch_recent_birthday_posts(pool, limit=30)
        lines = [
            f"{row['birthday_date']:%Y-%m-%d} "
            f"{await slack_display_name(row['slack_user_id'], record_get(row, 'email'))} — 발송완료"
            for row in rows
        ]
        await respond(
            text="\n".join(lines) if lines else "최근 발송 로그가 없어요.",
            response_type="ephemeral",
        )
        return

    if subcommand == "sync":
        if settings is None:
            await respond(text="동기화 설정을 찾을 수 없어요.", response_type="ephemeral")
            return

        runner = sync_runner
        if runner is None:
            from sync import sync_hr_sheet

            runner = sync_hr_sheet

        result = await runner(pool=pool, client=_slack_client, settings=settings)
        await respond(
            text=f"HR 시트 동기화 완료: {result.upserted}명 upsert, {result.deactivated}명 비활성화",
            response_type="ephemeral",
        )
        return

    if subcommand == "set":
        if len(raw_parts) != 4:
            await respond(
                text="사용법: `/birthday admin set @유저 MM-DD`",
                response_type="ephemeral",
            )
            return

        target_user_id = parse_slack_mention(raw_parts[2])
        birthday = parse_month_day(raw_parts[3])
        if target_user_id is None or birthday is None:
            await respond(
                text="사용법: `/birthday admin set @유저 MM-DD`",
                response_type="ephemeral",
            )
            return

        birth_month, birth_day = birthday
        await db.upsert_birthday(
            pool,
            slack_user_id=target_user_id,
            birth_month=birth_month,
            birth_day=birth_day,
            email=None,
        )
        await respond(
            text=f"<@{target_user_id}> 님의 생일을 {birth_month:02d}-{birth_day:02d}로 등록했습니다.",
            response_type="ephemeral",
        )
        return

    if subcommand == "reset-onboarding":
        if settings is None:
            await respond(text="온보딩 설정을 찾을 수 없어요.", response_type="ephemeral")
            return

        await reset_onboarding(pool=pool, client=_slack_client, settings=settings)
        await respond(
            text="온보딩 메시지를 초기화하고 재발송했습니다.",
            response_type="ephemeral",
        )
        return

    if subcommand == "test-birthday":
        if settings is None:
            await respond(text="테스트 발송 설정을 찾을 수 없어요.", response_type="ephemeral")
            return

        if len(raw_parts) != 3:
            await respond(
                text="사용법: `/birthday admin test-birthday @유저`",
                response_type="ephemeral",
            )
            return

        target_user_id = parse_slack_mention(raw_parts[2])
        if target_user_id is None:
            await respond(
                text="사용법: `/birthday admin test-birthday @유저`",
                response_type="ephemeral",
            )
            return

        await send_test_birthday(settings=settings, target_user_id=target_user_id)
        await respond(
            text=f"테스트 발송 완료: <@{target_user_id}>",
            response_type="ephemeral",
        )
        return

    if subcommand == "test-weekend":
        if settings is None:
            await respond(text="테스트 발송 설정을 찾을 수 없어요.", response_type="ephemeral")
            return

        if len(raw_parts) != 3:
            await respond(
                text="사용법: `/birthday admin test-weekend @유저`",
                response_type="ephemeral",
            )
            return

        target_user_id = parse_slack_mention(raw_parts[2])
        if target_user_id is None:
            await respond(
                text="사용법: `/birthday admin test-weekend @유저`",
                response_type="ephemeral",
            )
            return

        await send_test_weekend(settings=settings, target_user_id=target_user_id)
        await respond(
            text=f"주말 테스트 발송 완료: <@{target_user_id}>",
            response_type="ephemeral",
        )
        return

    await respond(
        text="사용 가능한 관리자 명령어: `/birthday admin list`, `/birthday admin log`, `/birthday admin sync`, `/birthday admin set @유저 MM-DD`, `/birthday admin reset-onboarding`, `/birthday admin test-birthday @유저`, `/birthday admin test-weekend @유저`",
        response_type="ephemeral",
    )


async def slack_display_name(slack_user_id: str, fallback: str | None = None) -> str:
    if _slack_client is None:
        return fallback or slack_user_id

    try:
        result = await _slack_client.users_info(user=slack_user_id)
    except Exception:
        return fallback or slack_user_id

    user = result.get("user") or {}
    profile = user.get("profile") or {}
    return (
        profile.get("real_name")
        or profile.get("display_name")
        or user.get("real_name")
        or user.get("name")
        or fallback
        or slack_user_id
    )


def record_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


def format_birthday_status(row: Any | None) -> str:
    if row is None:
        return "생일 미등록"
    return f"생일 등록됨 ({row['birth_month']:02d}-{row['birth_day']:02d})"


def parse_slack_mention(value: str) -> str | None:
    match = re.fullmatch(r"<@([A-Z0-9]+)>", value)
    return match.group(1) if match else None


def parse_month_day(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{2})-(\d{2})", value)
    if not match:
        return None

    month = int(match.group(1))
    day = int(match.group(2))
    if 1 <= month <= 12 and 1 <= day <= calendar.monthrange(2024, month)[1]:
        return month, day
    return None


async def reset_onboarding(*, pool: Any, client: Any, settings: Any) -> None:
    from onboarding import ONBOARDING_STATE_KEY, ensure_onboarding_message

    await db.delete_bot_state(pool, ONBOARDING_STATE_KEY)
    await ensure_onboarding_message(pool=pool, client=client, settings=settings)


async def send_test_birthday(*, settings: Any, target_user_id: str) -> None:
    from birthday import CHANNEL_MESSAGE, DM_MESSAGE

    await _slack_client.chat_postMessage(
        channel=settings.birthday_channel_id,
        text=CHANNEL_MESSAGE.format(slack_user_id=target_user_id),
    )
    await _slack_client.chat_postMessage(channel=target_user_id, text=DM_MESSAGE)


async def send_test_weekend(*, settings: Any, target_user_id: str) -> None:
    from birthday import DM_MESSAGE, WEEKEND_EARLY_MESSAGE

    text = WEEKEND_EARLY_MESSAGE.format(
        weekday_label="토요일",
        slack_user_id=target_user_id,
    )
    await _slack_client.chat_postMessage(channel=settings.birthday_channel_id, text=text)
    await _slack_client.chat_postMessage(channel=target_user_id, text=DM_MESSAGE)
