from __future__ import annotations

import asyncio
import logging
import os
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

import db
from commands import register_commands
from config import load_settings
from home import register_home
from onboarding import ensure_onboarding_message
from scheduler import create_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = load_settings()
slack_app = AsyncApp(token=settings.slack_bot_token)


async def monitor_socket_task(socket_task: asyncio.Task[object]) -> None:
    try:
        await socket_task
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.critical("Socket Mode task failed", exc_info=True)
        os.kill(os.getpid(), signal.SIGTERM)
        return

    logger.critical("Socket Mode task exited unexpectedly")
    os.kill(os.getpid(), signal.SIGTERM)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = None
    scheduler = None
    socket_task = None
    socket_monitor_task = None

    try:
        pool = await db.create_pool(settings.database_url)
        register_commands(slack_app, pool, settings)
        register_home(slack_app, pool, settings)
        await ensure_onboarding_message(pool=pool, client=slack_app.client, settings=settings)

        scheduler = create_scheduler(pool=pool, client=slack_app.client, settings=settings)
        scheduler.start()

        socket_handler = AsyncSocketModeHandler(slack_app, settings.slack_app_token)
        socket_task = asyncio.create_task(socket_handler.start_async())
        socket_monitor_task = asyncio.create_task(monitor_socket_task(socket_task))
        logger.info("Birthday bot started")

        app.state.pool = pool
        app.state.scheduler = scheduler
        app.state.socket_task = socket_task
        app.state.socket_monitor_task = socket_monitor_task

        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)

        tasks_to_cleanup = [task for task in (socket_task, socket_monitor_task) if task is not None]
        for task in tasks_to_cleanup:
            if not task.done():
                task.cancel()

        if tasks_to_cleanup:
            await asyncio.gather(*tasks_to_cleanup, return_exceptions=True)

        if pool is not None:
            await pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
