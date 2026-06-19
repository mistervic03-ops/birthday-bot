from __future__ import annotations

import logging

import asyncpg
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

import db
from config import Settings

logger = logging.getLogger(__name__)

ONBOARDING_STATE_KEY = "onboarding_message_ts"
ONBOARDING_MESSAGE = """🎂 안녕하세요, 저는 빅스데이예요!

매일 오전 9시, 생일인 분의 소식을 이 채널에 알려드리고 당사자분께 DM도 보내드려요.
HR 정보를 기반으로 자동으로 운영되니 별도 등록은 필요 없어요 😊

⚙️ 설정 커맨드 (본인에게만 보여요)
• `/birthday optout` — 내 생일 공지 끄기
• `/birthday optin`  — 다시 켜기
• `/birthday status` — 현재 설정 확인"""


async def ensure_onboarding_message(
    *, pool: asyncpg.Pool, client: AsyncWebClient, settings: Settings
) -> bool:
    if await db.get_bot_state(pool, ONBOARDING_STATE_KEY):
        return True

    try:
        result = await client.chat_postMessage(
            channel=settings.birthday_channel_id,
            text=ONBOARDING_MESSAGE,
        )
    except SlackApiError:
        logger.error("Failed to post onboarding message", exc_info=True)
        return False

    logger.info("온보딩 메시지 발송 완료")
    message_ts = result.get("ts")
    if message_ts is None:
        logger.warning("Onboarding message posted without ts; skipping pin")
        return False

    await db.set_bot_state(pool, ONBOARDING_STATE_KEY, message_ts)

    try:
        await client.pins_add(channel=settings.birthday_channel_id, timestamp=message_ts)
    except SlackApiError:
        logger.warning("Failed to pin onboarding message", exc_info=True)
        return True

    logger.info("핀 고정 완료")
    return True
