from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    slack_bot_token: str
    slack_app_token: str
    database_url: str
    birthday_channel_id: str
    google_sheets_id: str
    google_service_account_json: str
    timezone: str = "Asia/Seoul"


def load_settings() -> Settings:
    load_dotenv()

    return Settings(
        slack_bot_token=_required_env("SLACK_BOT_TOKEN"),
        slack_app_token=_required_env("SLACK_APP_TOKEN"),
        database_url=_required_env("DATABASE_URL"),
        birthday_channel_id=_required_env("BIRTHDAY_CHANNEL_ID"),
        google_sheets_id=_required_env("GOOGLE_SHEETS_ID"),
        google_service_account_json=_required_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

