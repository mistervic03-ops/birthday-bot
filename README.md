# Birthday Bot

Slack Socket Mode bot that syncs birthdays from an HR Excel file and sends daily birthday announcements and DMs.

## What It Does

- Reads HR birthday data from the Excel file configured by `HR_EXCEL_PATH`.
- Resolves Slack users by email with `users.lookupByEmail`.
- Stores active birthdays in Postgres.
- Sends birthday channel announcements at 09:00 KST.
- Sends a birthday DM to the birthday person.
- On Fridays, sends early announcements for Saturday and Sunday birthdays.
- Prevents duplicate birthday announcements with a Postgres send reservation, send log, and advisory lock.
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
   | `TIMEZONE` | No | Runtime timezone for scheduler and "today" calculations. Defaults to `Asia/Seoul`. |
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

Both jobs use the configured `TIMEZONE`, `coalesce=True`, and `max_instances=1`. If the server clock is UTC, the scheduler still evaluates the cron times in `Asia/Seoul` unless `TIMEZONE` is changed.

## Slack App Setup

Required bot token scopes:

- `chat:write` - post channel announcements and DMs.
- `commands` - receive the `/birthday` slash command.
- `users:read` - check active/admin users and resolve display names.
- `users:read.email` - resolve HR emails with `users.lookupByEmail`.
- `pins:write` - pin the onboarding message.

Required app-level token scope:

- `connections:write` - run Socket Mode.

Use a channel ID such as `C012345ABC` for `BIRTHDAY_CHANNEL_ID`, not a channel name. Invite the bot to the channel before starting the app.

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
- `/birthday admin log` - show recent birthday announcement logs, including failed/reserved sends.
- `/birthday admin sync` - manually sync the HR Excel file.
- `/birthday admin set @user MM-DD` - manually set a user's birthday.
- `/birthday admin reset-onboarding` - resend and pin the onboarding message.
- `/birthday admin preview YYYY-MM-DD` - preview who would be sent for a date without posting to Slack or writing send logs.
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
- `birthday_posts` - send reservation/log used to prevent duplicates. `status` is `sending`, `sent`, or `failed`.
- `bot_state` - small bot state values such as the onboarding message timestamp.

## Duplicate Sending and Recovery

The scheduled sender reserves `(slack_user_id, birthday_date)` in `birthday_posts` before posting to Slack. This blocks duplicate sends across process restarts, overlapping scheduler runs, and ambiguous Slack client failures.

Status flow:

- `sending`: DB reservation exists and blocks duplicates. This is normally brief, but can remain if Slack accepted the message and the later DB success update failed.
- `sent`: Slack channel post succeeded and the DB success update completed.
- `failed`: Slack posting raised an error after the reservation was created.

Any existing row for the same `(slack_user_id, birthday_date)` blocks automatic re-send, regardless of status. This is intentional: when Slack may have accepted a message but the app saw an error, blocking first is safer than duplicate announcements.

Check reserved, sent, and failed rows with:

```bash
/birthday admin log
```

Recovery flow for `failed` or stale `sending` rows:

1. Confirm in Slack whether the announcement actually appeared.
2. If it did appear, leave the row as-is or update it to `sent` manually.
3. If it did not appear and you want the next run/manual call to send it, delete that one row from `birthday_posts` for the affected `slack_user_id` and `birthday_date`, then rerun the job or handle it manually.

Example SQL:

```sql
DELETE FROM birthday_posts
WHERE slack_user_id = 'U012345ABC'
  AND birthday_date = DATE '2026-06-19'
  AND status IN ('failed', 'sending');
```

## Tests

Run the test suite:

```bash
python3 -m pytest -q
```

Current tests cover birthday date logic, Friday weekend announcements, command routing, admin permissions, manual admin actions, test-send commands, and Excel sync parsing.

Useful manual checks before deployment:

```bash
# Health check
curl http://127.0.0.1:8000/health

# Preview a date without posting to Slack
/birthday admin preview 2026-06-19

# Send an explicit test message to one Slack user
/birthday admin test-birthday @user
```

## Notes

- The project currently uses app-startup schema creation, not a migration tool.
- Integration tests for real Slack and Postgres are not included yet.
- Deployment files such as Dockerfile, docker-compose, and CI workflows are not included yet.
- For Spark deployment, run this as a long-lived FastAPI process, for example with `uvicorn main:app --host 0.0.0.0 --port 8000` under Spark's process manager or a service supervisor. Set all environment variables in Spark rather than relying on a local `.env` file.
