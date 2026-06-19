# Birthday Bot

Slack Socket Mode bot that syncs birthdays from an HR Excel file and sends daily birthday announcements and DMs.

## What It Does

- Reads HR birthday data from the Excel file configured by `HR_EXCEL_PATH`.
- Resolves Slack users by email with `users.lookupByEmail`.
- Stores active birthdays in Postgres.
- Sends birthday channel announcements at 09:00 KST.
- Sends a birthday DM to the birthday person.
- On Fridays, sends early announcements for Saturday and Sunday birthdays.
- Prevents duplicate birthday announcements with a Postgres send log and advisory lock.
- Lets users opt out of channel announcements.
- Provides admin commands for manual sync, inspection, testing, and overrides.
- Posts and pins a one-time onboarding message in the birthday channel.

## Setup

1. Create a Python 3.11+ environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in the values:

   ```bash
   cp .env.example .env
   ```

   Environment variables used by the current code:

   | Name | Required | Description |
   | --- | --- | --- |
   | `SLACK_BOT_TOKEN` | Yes | Slack bot token. |
   | `SLACK_APP_TOKEN` | Yes | Slack app-level token for Socket Mode. |
   | `DATABASE_URL` | Yes | Postgres connection URL. |
   | `BIRTHDAY_CHANNEL_ID` | Yes | Channel ID where birthday announcements are posted. |
   | `HR_EXCEL_PATH` | Yes | Path to the HR Excel file. Absolute paths and project-relative paths are both supported by the runtime environment. |
   | `ADMIN_USER_IDS` | No | Comma-separated Slack user IDs that should receive admin command access in addition to workspace Admins/Owners. |

4. Start the app:

   ```bash
   uvicorn main:app --reload
   ```

5. Check the health endpoint:

   ```bash
   curl http://127.0.0.1:8000/health
   ```

## Runtime Flow

When the app starts, it:

1. Loads settings from `.env`.
2. Connects to Postgres.
3. Creates missing tables if needed.
4. Registers the Slack `/birthday` command.
5. Sends the onboarding message if it has not already been sent.
6. Starts the scheduler.
7. Starts Slack Socket Mode and monitors the socket task.

Scheduled jobs:

- `08:50` KST: sync HR birthday data from the configured Excel file.
- `09:00` KST: send birthday announcements and DMs.

## HR Excel Columns

The configured Excel file should contain employee birthday rows. If a supported header row is present, the parser detects the birthday and email columns.

Supported header names:

- Birthday: `birthday`, `birthdate`, `생일`, `생년월일`
- Email: `email`, `이메일`

Supported birthday formats:

- Excel date cells
- `YYYY-MM-DD`
- `MM-DD`
- `MM/DD`

Rows without an email or with an invalid birthday are skipped. Rows whose email cannot be resolved to a Slack user are also skipped. If Slack lookup fails because of a non-recoverable API error, the current batch stops without soft-deleting existing birthdays.

## Slack Commands

User commands:

- `/birthday optout` - turn off channel birthday announcements for yourself.
- `/birthday optin` - turn channel birthday announcements back on.
- `/birthday status` - show your current birthday announcement preference and registered birthday status.

Admin commands:

- `/birthday admin list` - list active registered birthdays.
- `/birthday admin log` - show recent birthday announcement logs.
- `/birthday admin sync` - manually sync the HR Excel file.
- `/birthday admin set @user MM-DD` - manually set a user's birthday.
- `/birthday admin reset-onboarding` - resend and pin the onboarding message.
- `/birthday admin test-birthday @user` - send a test birthday announcement and DM.
- `/birthday admin test-weekend @user` - send a test weekend-style announcement and DM.

Admin commands are available to Slack workspace Admins/Owners or users listed in `ADMIN_USER_IDS`.

## Data Source Changes

The current production data source is an Excel file, but the sync pipeline is intentionally narrow:

1. A source reader produces `BirthdayRow(email, birth_month, birth_day)` records.
2. The sync job resolves each email to a Slack user ID.
3. The sync job upserts birthdays and marks missing records inactive.

To move to SharePoint or Microsoft Graph later, replace the source-reading portion that currently reads `settings.hr_excel_path` in `sync_hr_sheet()`. Keep returning the same `BirthdayRow` shape and the downstream Slack/DB logic can remain unchanged.

## Database Tables

The app creates these tables automatically on startup:

- `birthdays` - active birthday records keyed by Slack user ID.
- `user_preferences` - per-user birthday announcement preferences.
- `birthday_posts` - send log used to prevent duplicates.
- `bot_state` - small bot state values such as the onboarding message timestamp.

## Tests

Run the test suite:

```bash
python3 -m pytest -q
```

Current tests cover birthday date logic, Friday weekend announcements, command routing, admin permissions, manual admin actions, test-send commands, and Excel sync parsing.

## Notes

- The project currently uses app-startup schema creation, not a migration tool.
- Integration tests for real Slack and Postgres are not included yet.
- Deployment files such as Dockerfile, docker-compose, and CI workflows are not included yet.
