from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import db
from birthday_dates import birthday_targets_for
from commands import is_workspace_admin

logger = logging.getLogger(__name__)

HOME_CALLBACK_ID = "bigxday_home_v1"
OPTOUT_ACTION_ID = "bigxday_home_optout"
OPTIN_ACTION_ID = "bigxday_home_optin"


@dataclass(frozen=True)
class HomeData:
    birthday_record: Any | None
    receive_wishes: bool
    today_birthdays: list[Any]
    is_admin: bool = False
    active_birthday_count: int | None = None
    recent_log_summary: str | None = None


def register_home(app: Any, pool: Any, settings: Any) -> None:
    @app.event("app_home_opened")
    async def handle_app_home_opened(event, client):
        if event.get("tab") not in {None, "home"}:
            return

        user_id = event["user"]
        try:
            await publish_home(client=client, pool=pool, settings=settings, user_id=user_id)
        except Exception:
            logger.exception("Failed to publish Bigxday App Home for %s", user_id)

    @app.action(OPTOUT_ACTION_ID)
    async def handle_home_optout(ack, body, client):
        await ack()
        await update_receive_wishes_and_refresh(
            client=client,
            pool=pool,
            settings=settings,
            user_id=body["user"]["id"],
            receive_wishes=False,
        )

    @app.action(OPTIN_ACTION_ID)
    async def handle_home_optin(ack, body, client):
        await ack()
        await update_receive_wishes_and_refresh(
            client=client,
            pool=pool,
            settings=settings,
            user_id=body["user"]["id"],
            receive_wishes=True,
        )


async def update_receive_wishes_and_refresh(
    *,
    client: Any,
    pool: Any,
    settings: Any,
    user_id: str,
    receive_wishes: bool,
) -> None:
    try:
        await db.set_receive_wishes(pool, user_id, receive_wishes)
        await publish_home(client=client, pool=pool, settings=settings, user_id=user_id)
    except Exception:
        logger.exception("Failed to update Bigxday App Home preference for %s", user_id)


async def publish_home(*, client: Any, pool: Any, settings: Any, user_id: str) -> None:
    data = await load_home_data(pool=pool, settings=settings, user_id=user_id)
    await client.views_publish(user_id=user_id, view=build_home_view(data))


async def load_home_data(
    *,
    pool: Any,
    settings: Any,
    user_id: str,
    today: date | None = None,
) -> HomeData:
    today = today or _today_for_settings(settings)
    receive_wishes = await db.get_receive_wishes(pool, user_id)
    birthday_record = await db.fetch_active_birthday_for_user(pool, user_id)
    today_birthdays = [
        row
        for row in await db.fetch_birthdays_for_targets(pool, birthday_targets_for(today))
        if _record_get(row, "receive_wishes", True)
    ]

    is_admin = await is_workspace_admin(user_id, settings)
    if not is_admin:
        return HomeData(
            birthday_record=birthday_record,
            receive_wishes=receive_wishes,
            today_birthdays=today_birthdays,
        )

    active_birthdays = await db.fetch_active_birthdays(pool)
    recent_posts = await db.fetch_recent_birthday_posts(pool, limit=30)
    return HomeData(
        birthday_record=birthday_record,
        receive_wishes=receive_wishes,
        today_birthdays=today_birthdays,
        is_admin=True,
        active_birthday_count=len(active_birthdays),
        recent_log_summary=summarize_recent_logs(recent_posts),
    )


def build_home_view(data: HomeData) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "block_id": "bigxday_home_header",
            "text": {"type": "plain_text", "text": "🎂 Bigxday", "emoji": True},
        },
        {
            "type": "context",
            "block_id": "bigxday_home_description",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "오늘의 생일과 개인 설정을 확인하세요.",
                }
            ],
        },
        {"type": "divider"},
        status_dashboard_block(data),
        primary_action_block(data.receive_wishes),
        {"type": "divider"},
        today_birthdays_block(data.today_birthdays),
        how_it_works_block(),
        commands_block(),
    ]

    if data.is_admin:
        blocks.extend(admin_blocks(data))

    return {
        "type": "home",
        "callback_id": HOME_CALLBACK_ID,
        "blocks": blocks,
    }


def status_dashboard_block(data: HomeData) -> dict[str, Any]:
    if data.birthday_record is None:
        return {
            "type": "section",
            "block_id": "bigxday_home_status_dashboard",
            "text": {
                "type": "mrkdwn",
                "text": "*생일 정보가 아직 등록되지 않았습니다.*\nHR 명부 동기화 후 자동 반영됩니다.",
            },
            "fields": [
                {"type": "mrkdwn", "text": "*등록된 생일*\n미등록"},
                {"type": "mrkdwn", "text": f"*생일 공지*\n{receive_wishes_label(data.receive_wishes)}"},
            ],
        }

    month = _record_get(data.birthday_record, "birth_month")
    day = _record_get(data.birthday_record, "birth_day")
    return {
        "type": "section",
        "block_id": "bigxday_home_status_dashboard",
        "text": {"type": "mrkdwn", "text": "*내 생일 정보*"},
        "fields": [
            {"type": "mrkdwn", "text": f"*등록된 생일*\n{month:02d}월 {day:02d}일"},
            {"type": "mrkdwn", "text": f"*생일 공지*\n{receive_wishes_label(data.receive_wishes)}"},
        ],
    }


def primary_action_block(receive_wishes: bool) -> dict[str, Any]:
    if receive_wishes:
        button_text = "생일 공지 받지 않기"
        action_id = OPTOUT_ACTION_ID
        style = "danger"
        value = "optout"
    else:
        button_text = "생일 공지 다시 받기"
        action_id = OPTIN_ACTION_ID
        style = "primary"
        value = "optin"

    return {
        "type": "actions",
        "block_id": "bigxday_home_primary_action",
        "elements": [
            {
                "type": "button",
                "action_id": action_id,
                "style": style,
                "text": {"type": "plain_text", "text": button_text, "emoji": True},
                "value": value,
            }
        ],
    }


def today_birthdays_block(rows: list[Any]) -> dict[str, Any]:
    if rows:
        lines = [f"• <@{_record_get(row, 'slack_user_id')}>" for row in rows]
        text = "*🎉 오늘 생일인 동료*\n" + "\n".join(lines)
    else:
        text = "*🎉 오늘 생일인 동료*\n오늘 생일인 동료가 없습니다."

    return {
        "type": "section",
        "block_id": "bigxday_home_today",
        "text": {"type": "mrkdwn", "text": text},
    }


def how_it_works_block() -> dict[str, Any]:
    return {
        "type": "section",
        "block_id": "bigxday_home_how_it_works",
        "text": {
            "type": "mrkdwn",
            "text": "*운영 방식*\n• 매일 오전 9시 생일 공지\n• 주말 생일은 금요일에 미리 안내\n• HR 명부 기준 자동 동기화",
        },
    }


def commands_block() -> dict[str, Any]:
    return {
        "type": "section",
        "block_id": "bigxday_home_commands",
        "text": {
            "type": "mrkdwn",
            "text": "*명령어*\n`/birthday status`  현재 상태 확인\n`/birthday optout`  생일 공지 받지 않기\n`/birthday optin`  생일 공지 다시 받기",
        },
    }


def admin_blocks(data: HomeData) -> list[dict[str, Any]]:
    active_count = data.active_birthday_count if data.active_birthday_count is not None else 0
    recent_summary = data.recent_log_summary or "최근 발송 기록 없음"
    return [
        {"type": "divider"},
        {
            "type": "section",
            "block_id": "bigxday_home_admin_summary",
            "text": {
                "type": "mrkdwn",
                "text": f"*운영 상태*\n• 활성 생일자 수: {active_count}명\n• 최근 발송: {recent_summary}",
            },
        },
        {
            "type": "section",
            "block_id": "bigxday_home_admin_commands",
            "text": {
                "type": "mrkdwn",
                "text": "*운영 명령어*\n`/birthday admin sync`  HR 명부 동기화\n`/birthday admin preview YYYY-MM-DD`  발송 대상 미리보기\n`/birthday admin log`  최근 발송 로그\n`/birthday admin help`  관리자 도움말",
            },
        },
    ]


def summarize_recent_logs(rows: list[Any]) -> str:
    if not rows:
        return "최근 발송 기록 없음"

    sent = 0
    failed = 0
    sending = 0
    for row in rows:
        status = _record_get(row, "status", "sent")
        if status == "failed":
            failed += 1
        elif status == "sending":
            sending += 1
        else:
            sent += 1

    return f"성공 {sent}건, 실패 {failed}건, 예약 {sending}건"


def receive_wishes_label(receive_wishes: bool) -> str:
    return "받는 중" if receive_wishes else "받지 않음"


def _today_for_settings(settings: Any) -> date:
    timezone = getattr(settings, "timezone", "Asia/Seoul")
    return datetime.now(ZoneInfo(timezone)).date()


def _record_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError):
        return default
