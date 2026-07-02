from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    slack_bot_token: str
    slack_app_token: str
    database_url: str
    birthday_channel_id: str
    hr_excel_path: str
    admin_user_ids: list[str] = field(default_factory=list)
    timezone: str = "Asia/Seoul"


def load_settings() -> Settings:
    load_dotenv()

    return Settings(
        slack_bot_token=_required_env("SLACK_BOT_TOKEN"),
        slack_app_token=_required_env("SLACK_APP_TOKEN"),
        database_url=_required_env("DATABASE_URL"),
        birthday_channel_id=_channel_id_env("BIRTHDAY_CHANNEL_ID"),
        hr_excel_path=_required_env("HR_EXCEL_PATH"),
        admin_user_ids=_csv_env("ADMIN_USER_IDS"),
        timezone=_timezone_env("TIMEZONE", "Asia/Seoul"),
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _channel_id_env(name: str) -> str:
    value = _required_env(name)
    if not re.fullmatch(r"[CG][A-Z0-9]+", value):
        raise RuntimeError(f"Invalid Slack channel ID in {name}")
    return value


def _timezone_env(name: str, default: str) -> str:
    value = os.getenv(name) or default
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"Invalid timezone in {name}: {value}") from error
    return value
