from __future__ import annotations

import logging
from collections.abc import Awaitable

import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient

from birthday import send_today_birthdays
from config import Settings
from sync import sync_hr_sheet

logger = logging.getLogger(__name__)


async def safe_job(name: str, coro: Awaitable[object]) -> None:
    try:
        await coro
    except Exception as error:
        logger.critical("Scheduled job '%s' failed: %s", name, error.__class__.__name__)


async def run_sync_job(
    *, pool: asyncpg.Pool, client: AsyncWebClient, settings: Settings
) -> None:
    await safe_job("sync_hr_sheet", sync_hr_sheet(pool=pool, client=client, settings=settings))


async def run_birthday_job(
    *, pool: asyncpg.Pool, client: AsyncWebClient, settings: Settings
) -> None:
    await safe_job(
        "send_today_birthdays",
        send_today_birthdays(pool=pool, client=client, settings=settings),
    )


def create_scheduler(
    *, pool: asyncpg.Pool, client: AsyncWebClient, settings: Settings
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        timezone=settings.timezone,
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    scheduler.add_job(
        run_sync_job,
        "cron",
        id="sync_hr_sheet",
        replace_existing=True,
        hour=8,
        minute=50,
        misfire_grace_time=900,
        coalesce=True,
        max_instances=1,
        kwargs={"pool": pool, "client": client, "settings": settings},
    )
    scheduler.add_job(
        run_birthday_job,
        "cron",
        id="send_today_birthdays",
        replace_existing=True,
        hour=9,
        minute=0,
        misfire_grace_time=900,
        coalesce=True,
        max_instances=1,
        kwargs={"pool": pool, "client": client, "settings": settings},
    )
    return scheduler
