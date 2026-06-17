from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

import db
from commands import register_commands
from config import load_settings
from onboarding import ensure_onboarding_message
from scheduler import create_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = load_settings()
slack_app = AsyncApp(token=settings.slack_bot_token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await db.create_pool(settings.database_url)
    register_commands(slack_app, pool, settings)
    await ensure_onboarding_message(pool=pool, client=slack_app.client, settings=settings)

    scheduler = create_scheduler(pool=pool, client=slack_app.client, settings=settings)
    scheduler.start()

    socket_handler = AsyncSocketModeHandler(slack_app, settings.slack_app_token)
    socket_task = asyncio.create_task(socket_handler.start_async())
    logger.info("Birthday bot started")

    app.state.pool = pool
    app.state.scheduler = scheduler
    app.state.socket_task = socket_task

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        socket_task.cancel()
        await pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
