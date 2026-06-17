from __future__ import annotations

import asyncio
import calendar
import logging
from dataclasses import dataclass
from datetime import datetime

import asyncpg
from google.oauth2 import service_account
from googleapiclient.discovery import build
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

import db
from config import Settings

logger = logging.getLogger(__name__)

SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)
DEFAULT_RANGE = "A:Z"


@dataclass(frozen=True)
class BirthdayRow:
    email: str
    birth_month: int
    birth_day: int


@dataclass(frozen=True)
class SyncResult:
    upserted: int
    deactivated: int


async def sync_hr_sheet(
    *, pool: asyncpg.Pool, client: AsyncWebClient, settings: Settings
) -> SyncResult:
    rows = await read_sheet_rows(settings)
    active_slack_user_ids: list[str] = []
    upserted = 0

    for row in rows:
        slack_user_id = await resolve_slack_id(client, row.email)
        if slack_user_id is None:
            logger.warning("Skipping HR row with unmatched email: %s", row.email)
            continue

        await db.upsert_birthday(
            pool,
            slack_user_id=slack_user_id,
            birth_month=row.birth_month,
            birth_day=row.birth_day,
            email=row.email,
        )
        active_slack_user_ids.append(slack_user_id)
        upserted += 1

    if active_slack_user_ids:
        deactivated = await db.mark_missing_birthdays_inactive(pool, active_slack_user_ids)
    else:
        logger.warning("No Slack users resolved from HR sheet; skipping soft-delete step")
        deactivated = 0

    return SyncResult(upserted=upserted, deactivated=deactivated)


async def resolve_slack_id(client: AsyncWebClient, email: str) -> str | None:
    try:
        result = await client.users_lookupByEmail(email=email)
    except SlackApiError:
        return None

    user = result.get("user") or {}
    return user.get("id")


async def read_sheet_rows(settings: Settings) -> list[BirthdayRow]:
    values = await asyncio.to_thread(_read_sheet_values, settings)
    return parse_sheet_values(values)


def _read_sheet_values(settings: Settings) -> list[list[str]]:
    credentials = service_account.Credentials.from_service_account_file(
        settings.google_service_account_json,
        scopes=SCOPES,
    )
    service = build("sheets", "v4", credentials=credentials)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheets_id, range=DEFAULT_RANGE)
        .execute()
    )
    return result.get("values", [])


def parse_sheet_values(values: list[list[str]]) -> list[BirthdayRow]:
    if not values:
        return []

    headers = [cell.strip().lower() for cell in values[0]]
    rows: list[BirthdayRow] = []
    for raw_row in values[1:]:
        row = _row_dict(headers, raw_row)
        email = (row.get("email") or "").strip()
        if not email:
            continue

        birthday = _parse_birthday(row)
        if birthday is None:
            logger.warning("Skipping HR row with invalid birthday: %s", email)
            continue

        birth_month, birth_day = birthday
        rows.append(BirthdayRow(email=email, birth_month=birth_month, birth_day=birth_day))

    return rows


def _row_dict(headers: list[str], row: list[str]) -> dict[str, str]:
    return {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}


def _parse_birthday(row: dict[str, str]) -> tuple[int, int] | None:
    month = row.get("birth_month") or row.get("month")
    day = row.get("birth_day") or row.get("day")
    if month and day:
        return _valid_month_day(month, day)

    birthday = row.get("birthday") or row.get("birthdate")
    if not birthday:
        return None

    birthday = birthday.strip()
    for fmt in ("%Y-%m-%d", "%m-%d", "%m/%d"):
        try:
            parsed = datetime.strptime(birthday, fmt)
        except ValueError:
            continue
        return _valid_month_day(str(parsed.month), str(parsed.day))

    return None


def _valid_month_day(month: str, day: str) -> tuple[int, int] | None:
    try:
        month_int = int(month)
        day_int = int(day)
    except ValueError:
        return None

    if 1 <= month_int <= 12 and 1 <= day_int <= calendar.monthrange(2024, month_int)[1]:
        return month_int, day_int
    return None
