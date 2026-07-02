from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

try:
    import asyncpg
except ModuleNotFoundError:  # pragma: no cover - lets unit tests import this module before deps are installed.
    asyncpg = None  # type: ignore[assignment]

try:
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient
except ModuleNotFoundError:  # pragma: no cover
    class SlackApiError(Exception):
        pass

    AsyncWebClient = Any  # type: ignore[assignment]

import db
from birthday_dates import birthday_targets_for
from config import Settings
from utils import slack_error_reason

logger = logging.getLogger(__name__)

CHANNEL_MESSAGE = "🎂 오늘은 <@{slack_user_id}> 님의 생일이에요! 다 같이 축하해드려요 🎉"
WEEKEND_EARLY_MESSAGE = "🎂 이번 주 {weekday_label}은 <@{slack_user_id}> 님의 생일이에요! 미리 축하 메시지 남겨주세요 🎉"
DM_MESSAGE = "🎂 <@{slack_user_id}> 님, 생일 축하드려요! 오늘 하루 행복하게 보내세요 ☀️"
WEEKEND_EARLY_DM_MESSAGE = "🎂 <@{slack_user_id}> 님, 이번 주 {weekday_label}이 생일이시네요! 미리 축하드려요 🎉"


@dataclass(frozen=True)
class BirthdaySendTarget:
    birth_month: int
    birth_day: int
    birthday_date: date
    message: str


def birthday_send_targets_for(today: date) -> list[BirthdaySendTarget]:
    target_dates = [today]
    if today.weekday() == 4:
        target_dates.extend([today + timedelta(days=1), today + timedelta(days=2)])

    targets: list[BirthdaySendTarget] = []
    for target_date in target_dates:
        for month, day in birthday_targets_for(target_date):
            targets.append(
                BirthdaySendTarget(
                    birth_month=month,
                    birth_day=day,
                    birthday_date=target_date,
                    message=message_for_target(today, target_date),
                )
            )
    return targets


def message_for_target(today: date, target_date: date) -> str:
    if target_date == today:
        return CHANNEL_MESSAGE

    weekday_label = "토요일" if target_date.weekday() == 5 else "일요일"
    return WEEKEND_EARLY_MESSAGE.format(weekday_label=weekday_label, slack_user_id="{slack_user_id}")


def dm_message_for_target(today: date, target_date: date) -> str:
    if target_date == today:
        return DM_MESSAGE

    weekday_label = "토요일" if target_date.weekday() == 5 else "일요일"
    return WEEKEND_EARLY_DM_MESSAGE.format(
        weekday_label=weekday_label,
        slack_user_id="{slack_user_id}",
    )


async def is_active_slack_member(client: AsyncWebClient, slack_user_id: str) -> bool:
    try:
        result = await client.users_info(user=slack_user_id)
    except SlackApiError:
        logger.warning("Failed to look up Slack user")
        return False

    user = result["user"]
    return not user.get("deleted", False) and not user.get("is_bot", False)


async def send_today_birthdays(
    *,
    pool: asyncpg.Pool,
    client: AsyncWebClient,
    settings: Settings,
    today: date | None = None,
) -> None:
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    send_targets = birthday_send_targets_for(today)
    targets_by_birthday = {
        (target.birth_month, target.birth_day): target for target in send_targets
    }
    try:
        rows = await db.fetch_birthdays_for_targets(
            pool, [(target.birth_month, target.birth_day) for target in send_targets]
        )
    except Exception:
        logger.error("Failed to fetch birthday send targets", exc_info=True)
        return

    for row in rows:
        slack_user_id = row["slack_user_id"]
        target = targets_by_birthday[(row["birth_month"], row["birth_day"])]
        birthday_date = target.birthday_date
        async with db.birthday_send_lock(pool, slack_user_id, birthday_date) as locked:
            if not locked:
                logger.info("Skipping locked birthday send")
                continue

            if await db.has_birthday_post(pool, slack_user_id, birthday_date):
                continue

            if not row["receive_wishes"]:
                logger.info("Skipping birthday for opted-out user")
                continue

            if not await is_active_slack_member(client, slack_user_id):
                logger.info("Skipping inactive Slack user")
                continue

            reserved = await db.reserve_birthday_post(pool, slack_user_id, birthday_date)
            if not reserved:
                logger.info("Skipping already reserved birthday send")
                continue

            try:
                result = await client.chat_postMessage(
                    channel=settings.birthday_channel_id,
                    text=target.message.format(slack_user_id=slack_user_id),
                    username="빅스데이",
                )
            except Exception as error:
                try:
                    await db.mark_birthday_post_failed(
                        pool,
                        slack_user_id,
                        birthday_date,
                        error=slack_error_reason(error),
                    )
                except Exception:
                    logger.critical(
                        "Failed to mark birthday announcement failure",
                    )
                logger.warning("Failed to post birthday announcement: %s", slack_error_reason(error))
                continue

            try:
                await db.mark_birthday_post_sent(
                    pool,
                    slack_user_id,
                    birthday_date,
                    channel_ts=result.get("ts") if result is not None else None,
                )
            except Exception:
                logger.critical(
                    "공지는 갔지만 성공 상태 업데이트 실패 — birthday_posts 예약으로 자동 중복 발송은 차단됨",
                )
                continue

            try:
                await send_birthday_dm(client, slack_user_id, dm_message_for_target(today, birthday_date))
            except Exception as error:
                reason = slack_error_reason(error)
                try:
                    await db.mark_birthday_dm_failed(
                        pool,
                        slack_user_id,
                        birthday_date,
                        error=reason,
                    )
                except Exception as db_error:
                    logger.critical(
                        "Failed to mark birthday DM failure: %s",
                        db_error.__class__.__name__,
                    )
                logger.warning("Failed to send birthday DM: %s", reason)
                continue

            try:
                await db.mark_birthday_dm_sent(pool, slack_user_id, birthday_date)
            except Exception as error:
                logger.critical(
                    "Failed to mark birthday DM success: %s",
                    error.__class__.__name__,
                )


async def send_birthday_dm(client: AsyncWebClient, slack_user_id: str, message: str = DM_MESSAGE) -> None:
    await client.chat_postMessage(channel=slack_user_id, text=message.format(slack_user_id=slack_user_id))
