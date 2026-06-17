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

logger = logging.getLogger(__name__)

CHANNEL_MESSAGE = "🎂 오늘은 <@{slack_user_id}> 님의 생일입니다! 다 같이 축하해줘요 🎉"
WEEKEND_EARLY_MESSAGE = "🎂 이번 주 {weekday_label}은 <@{slack_user_id}> 님의 생일이에요! 미리 축하 메시지 남겨주세요 🎉"
DM_MESSAGE = "생일 축하해요 🎂 오늘 하루 즐겁게 보내세요!"


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


async def is_active_slack_member(client: AsyncWebClient, slack_user_id: str) -> bool:
    try:
        result = await client.users_info(user=slack_user_id)
    except SlackApiError:
        logger.warning("Failed to look up Slack user %s", slack_user_id, exc_info=True)
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
    rows = await db.fetch_birthdays_for_targets(
        pool, [(target.birth_month, target.birth_day) for target in send_targets]
    )

    for row in rows:
        slack_user_id = row["slack_user_id"]
        target = targets_by_birthday[(row["birth_month"], row["birth_day"])]
        birthday_date = target.birthday_date
        async with db.birthday_send_lock(pool, slack_user_id, birthday_date) as locked:
            if not locked:
                logger.info("Skipping locked birthday send for %s", slack_user_id)
                continue

            if await db.has_birthday_post(pool, slack_user_id, birthday_date):
                continue

            if not row["receive_wishes"]:
                logger.info("Skipping birthday for opted-out user %s", slack_user_id)
                continue

            if not await is_active_slack_member(client, slack_user_id):
                logger.info("Skipping inactive Slack user %s", slack_user_id)
                continue

            try:
                await client.chat_postMessage(
                    channel=settings.birthday_channel_id,
                    text=target.message.format(slack_user_id=slack_user_id),
                )
            except SlackApiError:
                logger.exception("Failed to post birthday announcement for %s", slack_user_id)
                continue

            await db.record_birthday_post(pool, slack_user_id, birthday_date)
            await send_birthday_dm(client, slack_user_id)


async def send_birthday_dm(client: AsyncWebClient, slack_user_id: str) -> None:
    try:
        await client.chat_postMessage(channel=slack_user_id, text=DM_MESSAGE)
    except SlackApiError:
        logger.warning("Failed to send birthday DM to %s", slack_user_id, exc_info=True)
