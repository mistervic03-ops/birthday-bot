# Birthday Bot MVP

Slack Socket Mode bot that syncs birthdays from an HR Google Sheet and sends daily 09:00 KST birthday announcements and DMs.

## What It Does

- Syncs birthday data from a Google Sheet into Postgres.
- Resolves Slack users by email from the HR sheet.
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

   Required environment variables:

   - `SLACK_BOT_TOKEN`
   - `SLACK_APP_TOKEN`
   - `DATABASE_URL`
   - `BIRTHDAY_CHANNEL_ID`
   - `GOOGLE_SHEETS_ID`
   - `GOOGLE_SERVICE_ACCOUNT_JSON`

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
7. Starts Slack Socket Mode.

Scheduled jobs:

- `08:50` KST: sync HR Google Sheet data.
- `09:00` KST: send birthday announcements and DMs.

## HR Sheet Columns

The first row should contain headers. Supported formats:

- `email`, `birth_month`, `birth_day`
- `email`, `birthday` with `YYYY-MM-DD`, `MM-DD`, or `MM/DD`
- `email`, `birthdate` with `YYYY-MM-DD`, `MM-DD`, or `MM/DD`

Rows without an email or with an invalid birthday are skipped. Rows whose email cannot be resolved to a Slack user are also skipped.

## Slack Commands

User commands:

- `/birthday optout` - turn off channel birthday announcements for yourself.
- `/birthday optin` - turn channel birthday announcements back on.
- `/birthday status` - show your current birthday announcement preference and registered birthday status.

Admin commands:

- `/birthday admin list` - list active registered birthdays.
- `/birthday admin log` - show recent birthday announcement logs.
- `/birthday admin sync` - manually sync the HR Google Sheet.
- `/birthday admin set @user MM-DD` - manually set a user's birthday.
- `/birthday admin reset-onboarding` - resend and pin the onboarding message.
- `/birthday admin test-birthday @user` - send a test birthday announcement and DM.
- `/birthday admin test-weekend @user` - send a test weekend-style announcement and DM.

Admin commands are available only to Slack workspace admins or owners.

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

Current tests cover birthday date logic, Friday weekend announcements, command routing, admin permissions, manual admin actions, and test-send commands.

## Notes

- The project currently uses app-startup schema creation, not a migration tool.
- Integration tests for real Slack, Google Sheets, and Postgres are not included yet.
- Deployment files such as Dockerfile, docker-compose, and CI workflows are not included yet.
