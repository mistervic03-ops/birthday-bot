from __future__ import annotations

import logging

import asyncpg
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

import db
from config import Settings

logger = logging.getLogger(__name__)

ONBOARDING_STATE_KEY = "onboarding_message_ts"
ONBOARDING_MESSAGE = """🎂 생일 축하봇이 생겼어요!

HR 정보를 기반으로 매일 오전 9시, 생일인 분의 채널 공지와 DM을 자동으로 보내드려요.

⚙️ 설정 커맨드 (본인에게만 보여요)
• `/birthday optout` — 내 생일 채널 공지 끄기
• `/birthday optin` — 다시 켜기
• `/birthday status` — 현재 설정 확인"""


async def ensure_onboarding_message(
    *, pool: asyncpg.Pool, client: AsyncWebClient, settings: Settings
) -> None:
    if await db.get_bot_state(pool, ONBOARDING_STATE_KEY):
        return

    try:
        result = await client.chat_postMessage(
            channel=settings.birthday_channel_id,
            text=ONBOARDING_MESSAGE,
        )
    except SlackApiError:
        logger.warning("Failed to post onboarding message", exc_info=True)
        return

    message_ts = result["ts"]
    await db.set_bot_state(pool, ONBOARDING_STATE_KEY, message_ts)

    try:
        await client.pins_add(channel=settings.birthday_channel_id, timestamp=message_ts)
    except SlackApiError:
        logger.warning("Failed to pin onboarding message", exc_info=True)
