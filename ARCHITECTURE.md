# Architecture

## Overview

Birthday Bot is a FastAPI app that runs a Slack Socket Mode bot, an APScheduler scheduler, and a Postgres-backed birthday store in one process.

The app has four main runtime paths:

- Startup and lifecycle management in `main.py`
- Scheduled jobs in `scheduler.py`
- Birthday sync from HR data in `sync.py`
- Birthday announcement sending in `birthday.py`
- Slack slash command handling in `commands.py`

## Configuration

Runtime settings are loaded from `.env` by `config.load_settings()`.

Required settings in the current code:

- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `DATABASE_URL`
- `BIRTHDAY_CHANNEL_ID`
- `HR_EXCEL_PATH`
- `TIMEZONE` (optional, defaults to `Asia/Seoul`)
- `ADMIN_USER_IDS` (optional, comma-separated Slack user IDs)

`config.py` parses `ADMIN_USER_IDS` into `settings.admin_user_ids`. Missing or empty values become an empty list.

## Spark Deployment

The current Spark deployment runs as a single systemd service:

- Host: `bigxdata@192.168.3.41`
- App directory: `/home/bigxdata/birthday-bot`
- Service: `birthday-bot.service`
- Environment file: `/home/bigxdata/birthday-bot/.env`
- ExecStart: `/home/bigxdata/birthday-bot/.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8010`
- Health endpoint: `http://127.0.0.1:8010/health`
- Database: local PostgreSQL database `birthday_bot` with user `birthday_bot`
- HR Excel file: `/home/bigxdata/birthday-bot/data/hr_birthdays.xlsx`

Operational changes should only target `birthday-bot.service` and files under `/home/bigxdata/birthday-bot`.

## Startup

`main.py` creates the Slack app and FastAPI app.

During FastAPI lifespan startup, it:

1. Creates the asyncpg pool with `db.create_pool()`.
2. Registers slash commands with `commands.register_commands()`.
3. Sends the onboarding message with `onboarding.ensure_onboarding_message()`.
4. Creates and starts the scheduler with `scheduler.create_scheduler()`.
5. Starts Slack Socket Mode.
6. Starts a monitor task that logs a critical error and terminates the process if Socket Mode exits unexpectedly.

The lifespan cleanup path shuts down the scheduler, cancels socket tasks, and closes the DB pool.

## HR Sync Pipeline

The scheduled sync entrypoint is:

```python
sync_hr_sheet(pool=pool, client=client, settings=settings)
```

The signature is intentionally stable because `scheduler.py` and the admin command in `commands.py` call it directly.

Current flow:

1. `sync_hr_sheet()` reads the Excel file at `settings.hr_excel_path`.
2. `read_excel_rows()` parses birthday rows into `BirthdayRow` objects.
3. For each row, `resolve_slack_id_for_batch()` resolves the email to a Slack user ID.
4. Valid rows are upserted into `birthdays`.
5. If at least one Slack user was resolved, records missing from the latest HR file are marked inactive.

Slack lookup behavior:

- `users_not_found`: skip the row and log a warning.
- `ratelimited`: wait for `Retry-After`, retry once, then continue or abort based on the retry result.
- Other Slack API errors: log an error and abort the batch without soft-deleting existing records.

## HR Excel Parser

`read_excel_rows()` uses `openpyxl` and supports:

- Header-based column detection for birthday and email columns.
- Excel date cells.
- Birthday strings in `YYYY-MM-DD`, `MM-DD`, or `MM/DD` format.

The lower-level `parse_rows()` helper accepts sheet-like `list[list[str]]` data and returns `BirthdayRow` records. It is source-agnostic and can be reused by future API readers that produce tabular values.

## Future Source Migration

The downstream sync contract is `list[BirthdayRow]`.

To move from local Excel to SharePoint or Microsoft Graph:

1. Add a source reader that downloads or reads the HR workbook/table.
2. Convert that data to `BirthdayRow` records, either by reusing `parse_rows()` for table-like values or by constructing `BirthdayRow` directly.
3. Change only the source-read step in `sync_hr_sheet()` so it uses the new reader instead of `read_excel_rows(settings.hr_excel_path)`.
4. Keep the Slack lookup, DB upsert, and inactive-marking logic unchanged.

This keeps source-specific authentication and API code isolated from the birthday sync behavior.

## Slack Commands

`commands.py` registers `/birthday`.

User commands:

- `optout`
- `optin`
- `status`

Admin commands:

- `admin list`
- `admin log`
- `admin sync`
- `admin set @user MM-DD`
- `admin reset-onboarding`
- `admin test-birthday @user`
- `admin test-weekend @user`

Admin access uses an OR policy:

1. If the caller's Slack user ID is in `settings.admin_user_ids`, access is granted without calling Slack.
2. Otherwise, `commands.py` calls Slack `users_info` and grants access to workspace Admins or Owners via `is_admin` or `is_owner`.

This allows operations to grant bot admin rights to specific Slack users even when they are not workspace Admins/Owners, while preserving Slack workspace role checks as the default fallback.

Mention parsing supports both `<@U...>` and `<@U...|name>` forms, plus case-insensitive `@username` lookup against Slack `name`, `display_name`, and `real_name`.

## Birthday Sending

`birthday.py` handles the scheduled announcement job.

The sender:

- Selects today's birthdays.
- On Fridays, includes Saturday and Sunday targets.
- Uses advisory locks and `birthday_posts` to avoid duplicate sends.
- Reserves the channel announcement in `birthday_posts` before posting to Slack.
- Marks the reservation `sent` after Slack returns success.
- Marks the reservation `failed` if Slack returns an API error; automatic retries are blocked until an operator clears or updates the row.
- Sends the birthday DM after the channel post is recorded.

## Database

`db.py` creates tables on startup if they do not already exist:

- `birthdays`
- `user_preferences`
- `birthday_posts`
- `bot_state`

The app does not currently use a migration tool. Schema drift must be handled manually.
