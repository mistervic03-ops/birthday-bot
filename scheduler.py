from __future__ import annotations

import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient

from birthday import send_today_birthdays
from config import Settings
from sync import sync_hr_sheet


def create_scheduler(
    *, pool: asyncpg.Pool, client: AsyncWebClient, settings: Settings
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    scheduler.add_job(
        sync_hr_sheet,
        "cron",
        hour=8,
        minute=50,
        kwargs={"pool": pool, "client": client, "settings": settings},
    )
    scheduler.add_job(
        send_today_birthdays,
        "cron",
        hour=9,
        minute=0,
        kwargs={"pool": pool, "client": client, "settings": settings},
    )
    return scheduler

