from __future__ import annotations

import asyncio
import calendar
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime

try:
    import asyncpg
except ModuleNotFoundError:  # pragma: no cover - lets pure unit tests import helpers before deps are installed.
    asyncpg = None  # type: ignore[assignment]

try:
    from openpyxl import load_workbook
except ModuleNotFoundError:  # pragma: no cover - checked when Excel sync runs.
    load_workbook = None  # type: ignore[assignment]

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
except ModuleNotFoundError:  # pragma: no cover - checked when Google Sheets sync runs.
    service_account = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

import db
from config import Settings, load_settings

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


@dataclass(frozen=True)
class ExcelSyncResult:
    upserted: int
    skipped: int


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


def sync_from_excel(file_path: str) -> ExcelSyncResult:
    return asyncio.run(_sync_from_excel(file_path))


async def _sync_from_excel(file_path: str) -> ExcelSyncResult:
    rows, skipped = read_excel_rows(file_path)
    settings = load_settings()
    pool = await db.create_pool(settings.database_url)
    client = AsyncWebClient(token=settings.slack_bot_token)

    try:
        upserted = 0
        for row in rows:
            slack_user_id = await resolve_slack_id(client, row.email)
            if slack_user_id is None:
                logger.warning("Skipping Excel row with unmatched email: %s", row.email)
                skipped += 1
                continue

            await db.upsert_birthday(
                pool,
                slack_user_id=slack_user_id,
                birth_month=row.birth_month,
                birth_day=row.birth_day,
                email=row.email,
            )
            upserted += 1
    finally:
        await pool.close()

    return ExcelSyncResult(upserted=upserted, skipped=skipped)


async def resolve_slack_id(client: AsyncWebClient, email: str) -> str | None:
    try:
        result = await client.users_lookupByEmail(email=email)
    except SlackApiError:
        return None

    user = result.get("user") or {}
    return user.get("id")


async def read_sheet_rows(settings: Settings) -> list[BirthdayRow]:
    if settings.google_sheets_id is None or settings.google_service_account_json is None:
        logger.error("Skipping Google Sheets sync: Google Sheets settings are not configured")
        return []

    values = await asyncio.to_thread(_read_sheet_values, settings)
    return parse_sheet_values(values)


def read_excel_rows(file_path: str) -> tuple[list[BirthdayRow], int]:
    if load_workbook is None:
        raise RuntimeError("openpyxl is required to read Excel files")

    workbook = load_workbook(file_path, read_only=True, data_only=True)
    try:
        rows: list[BirthdayRow] = []
        skipped = 0
        birthday_index = 0
        email_index = 2
        columns_detected = False
        for index, raw_row in enumerate(workbook.active.iter_rows(values_only=True), start=1):
            if not columns_detected:
                header_indexes = _excel_header_indexes(raw_row)
                if header_indexes is not None:
                    birthday_index, email_index = header_indexes
                    columns_detected = True
                    continue
                columns_detected = True

            if not any(raw_row):
                continue

            email = _excel_cell_text(_excel_row_value(raw_row, email_index))
            birthday = _excel_birthday_mm_dd(_excel_row_value(raw_row, birthday_index))
            if not email or birthday is None:
                logger.warning("Skipping Excel row %s with missing email or birthday", index)
                skipped += 1
                continue

            month_day = _parse_mm_dd(birthday)
            if month_day is None:
                logger.warning("Skipping Excel row %s with invalid birthday: %s", index, birthday)
                skipped += 1
                continue

            birth_month, birth_day = month_day
            rows.append(BirthdayRow(email=email, birth_month=birth_month, birth_day=birth_day))

        return rows, skipped
    finally:
        workbook.close()


def _read_sheet_values(settings: Settings) -> list[list[str]]:
    if service_account is None or build is None:
        raise RuntimeError("Google API dependencies are required to sync Google Sheets")
    if settings.google_sheets_id is None or settings.google_service_account_json is None:
        logger.error("Skipping Google Sheets sync: Google Sheets settings are not configured")
        return []

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


def _excel_cell_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _excel_row_value(row: tuple[object, ...], index: int) -> object | None:
    return row[index] if index < len(row) else None


def _excel_header_indexes(row: tuple[object, ...]) -> tuple[int, int] | None:
    headers = [_excel_cell_text(value).lower() for value in row]
    birthday_names = {"birthday", "birthdate", "생일", "생년월일"}
    email_names = {"email", "이메일"}

    birthday_index = next(
        (index for index, header in enumerate(headers) if header in birthday_names), None
    )
    email_index = next((index for index, header in enumerate(headers) if header in email_names), None)
    if birthday_index is None or email_index is None:
        return None
    return birthday_index, email_index


def _excel_birthday_mm_dd(value: object) -> str | None:
    if isinstance(value, (datetime, date)):
        return f"{value.month:02d}-{value.day:02d}"

    birthday = _excel_cell_text(value)[:5]
    return birthday if len(birthday) == 5 else None


def _parse_mm_dd(value: str) -> tuple[int, int] | None:
    parts = value.split("-", maxsplit=1)
    if len(parts) != 2:
        return None
    return _valid_month_day(parts[0], parts[1])


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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = sys.argv[1:] if argv is None else argv
    if not args:
        raise SystemExit("Usage: python sync.py <excel-file>")

    result = sync_from_excel(args[0])
    print(f"Excel sync complete: {result.upserted} upserted, {result.skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
