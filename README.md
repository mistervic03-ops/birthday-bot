# Birthday Bot MVP

Slack Socket Mode bot that syncs birthdays from an HR Google Sheet and sends daily 09:00 KST birthday announcements and DMs.

## Setup

1. Create a Python 3.11+ environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in Slack, Postgres, and Google Sheets values.
4. Start the app:

   ```bash
   uvicorn main:app --reload
   ```

## HR Sheet Columns

The first row should contain headers. Supported birthday formats:

- `email`, `birth_month`, `birth_day`
- `email`, `birthday` with `YYYY-MM-DD`, `MM-DD`, or `MM/DD`
- `email`, `birthdate` with `YYYY-MM-DD`, `MM-DD`, or `MM/DD`

## Slack Command

- `/birthday optout`
- `/birthday optin`
- `/birthday status`

