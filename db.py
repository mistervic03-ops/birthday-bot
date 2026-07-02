from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import date

try:
    import asyncpg
except ModuleNotFoundError:  # pragma: no cover - lets pure unit tests import helpers before deps are installed.
    asyncpg = None  # type: ignore[assignment]


CREATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS birthdays (
    slack_user_id   VARCHAR(20) PRIMARY KEY,
    birth_month     SMALLINT NOT NULL CHECK (birth_month BETWEEN 1 AND 12),
    birth_day       SMALLINT NOT NULL CHECK (birth_day BETWEEN 1 AND 31),
    email           VARCHAR(100),
    source          VARCHAR(20) NOT NULL DEFAULT 'hr' CHECK (source IN ('hr', 'manual')),
    is_active       BOOLEAN DEFAULT TRUE,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_preferences (
    slack_user_id   VARCHAR(20) PRIMARY KEY,
    receive_wishes  BOOLEAN DEFAULT TRUE,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS birthday_posts (
    slack_user_id   VARCHAR(20) NOT NULL,
    birthday_date   DATE NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'sent' CHECK (status IN ('sending', 'sent', 'failed')),
    channel_ts      TEXT,
    error           TEXT,
    dm_status       VARCHAR(20) CHECK (dm_status IN ('sent', 'failed')),
    dm_error        TEXT,
    posted_at       TIMESTAMPTZ DEFAULT NOW(),
    sent_at         TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (slack_user_id, birthday_date)
);

CREATE TABLE IF NOT EXISTS bot_state (
    key             VARCHAR(100) PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
"""

MIGRATE_SCHEMA_SQL = """
ALTER TABLE birthday_posts
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'sent',
    ADD COLUMN IF NOT EXISTS channel_ts TEXT,
    ADD COLUMN IF NOT EXISTS error TEXT,
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

UPDATE birthday_posts
SET sent_at = COALESCE(sent_at, posted_at),
    updated_at = COALESCE(updated_at, posted_at, NOW())
WHERE status = 'sent';

DO $$
DECLARE
    source_column_exists BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'birthdays'
          AND column_name = 'source'
    ) INTO source_column_exists;

    IF NOT source_column_exists THEN
        ALTER TABLE birthdays ADD COLUMN source VARCHAR(20);

        UPDATE birthdays
        SET source = CASE
            WHEN email IS NULL THEN 'manual'
            ELSE 'hr'
        END;

        UPDATE birthdays
        SET is_active = TRUE,
            updated_at = NOW()
        WHERE source = 'manual'
          AND is_active = FALSE;

        ALTER TABLE birthdays ALTER COLUMN source SET DEFAULT 'hr';
        ALTER TABLE birthdays ALTER COLUMN source SET NOT NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'birthday_posts_status_check'
    ) THEN
        ALTER TABLE birthday_posts
            ADD CONSTRAINT birthday_posts_status_check
            CHECK (status IN ('sending', 'sent', 'failed'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'birthdays_source_check'
    ) THEN
        ALTER TABLE birthdays
            ADD CONSTRAINT birthdays_source_check
            CHECK (source IN ('hr', 'manual'));
    END IF;
END $$;
"""


async def create_pool(database_url: str) -> asyncpg.Pool:
    if asyncpg is None:
        raise RuntimeError("asyncpg is required to create a database pool")
    pool = await asyncpg.create_pool(database_url)
    await init_db(pool)
    return pool


async def init_db(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SCHEMA_SQL)
        await conn.execute(MIGRATE_SCHEMA_SQL)


async def upsert_birthday(
    pool: asyncpg.Pool,
    *,
    slack_user_id: str,
    birth_month: int,
    birth_day: int,
    email: str | None,
    source: str = "hr",
) -> None:
    if source not in {"hr", "manual"}:
        raise ValueError(f"Invalid birthday source: {source}")

    await pool.execute(
        """
        INSERT INTO birthdays (slack_user_id, birth_month, birth_day, email, source, is_active, updated_at)
        VALUES ($1, $2, $3, $4, $5, TRUE, NOW())
        ON CONFLICT (slack_user_id) DO UPDATE
        SET birth_month = EXCLUDED.birth_month,
            birth_day = EXCLUDED.birth_day,
            email = EXCLUDED.email,
            source = EXCLUDED.source,
            is_active = TRUE,
            updated_at = NOW()
        """,
        slack_user_id,
        birth_month,
        birth_day,
        email,
        source,
    )


async def mark_missing_birthdays_inactive(
    pool: asyncpg.Pool, active_slack_user_ids: Iterable[str]
) -> int:
    active_ids = list(active_slack_user_ids)
    return await pool.fetchval(
        """
        WITH updated AS (
            UPDATE birthdays
            SET is_active = FALSE, updated_at = NOW()
            WHERE is_active = TRUE
              AND source = 'hr'
              AND NOT (slack_user_id = ANY($1::varchar[]))
            RETURNING 1
        )
        SELECT COUNT(*) FROM updated
        """,
        active_ids,
    )


async def fetch_active_birthdays(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        """
        SELECT slack_user_id, birth_month, birth_day, email
        FROM birthdays
        WHERE is_active = TRUE
        ORDER BY birth_month ASC, birth_day ASC, slack_user_id ASC
        """
    )


async def fetch_recent_birthday_posts(pool: asyncpg.Pool, limit: int = 30) -> list[asyncpg.Record]:
    return await pool.fetch(
        """
        SELECT
            p.slack_user_id,
            p.birthday_date,
            COALESCE(p.sent_at, p.posted_at) AS posted_at,
            p.status,
            p.error,
            p.dm_status,
            p.dm_error,
            b.email
        FROM birthday_posts p
        LEFT JOIN birthdays b ON b.slack_user_id = p.slack_user_id
        ORDER BY COALESCE(p.sent_at, p.posted_at) DESC
        LIMIT $1
        """,
        limit,
    )


async def fetch_active_birthday_for_user(pool: asyncpg.Pool, slack_user_id: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT slack_user_id, birth_month, birth_day, email
        FROM birthdays
        WHERE slack_user_id = $1 AND is_active = TRUE
        """,
        slack_user_id,
    )


async def fetch_birthdays_for_targets(
    pool: asyncpg.Pool, targets: Sequence[tuple[int, int]]
) -> list[asyncpg.Record]:
    if not targets:
        return []

    months = [month for month, _ in targets]
    days = [day for _, day in targets]
    return await pool.fetch(
        """
        SELECT
            b.slack_user_id,
            b.birth_month,
            b.birth_day,
            b.email,
            b.is_active,
            COALESCE(p.receive_wishes, TRUE) AS receive_wishes
        FROM birthdays b
        LEFT JOIN user_preferences p ON p.slack_user_id = b.slack_user_id
        WHERE b.is_active = TRUE
          AND (b.birth_month, b.birth_day) IN (
              SELECT * FROM UNNEST($1::smallint[], $2::smallint[])
          )
        ORDER BY b.slack_user_id
        """,
        months,
        days,
    )


async def has_birthday_post(pool: asyncpg.Pool, slack_user_id: str, birthday_date: date) -> bool:
    return (
        await pool.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM birthday_posts
                WHERE slack_user_id = $1 AND birthday_date = $2
            )
            """,
            slack_user_id,
            birthday_date,
        )
        is True
    )


async def record_birthday_post(
    pool: asyncpg.Pool, slack_user_id: str, birthday_date: date
) -> bool:
    return await reserve_birthday_post(pool, slack_user_id, birthday_date)


async def reserve_birthday_post(
    pool: asyncpg.Pool, slack_user_id: str, birthday_date: date
) -> bool:
    status = await pool.execute(
        """
        INSERT INTO birthday_posts (slack_user_id, birthday_date, status, error, posted_at, updated_at)
        VALUES ($1, $2, 'sending', NULL, NOW(), NOW())
        ON CONFLICT DO NOTHING
        """,
        slack_user_id,
        birthday_date,
    )
    return status == "INSERT 0 1"


async def mark_birthday_post_sent(
    pool: asyncpg.Pool,
    slack_user_id: str,
    birthday_date: date,
    *,
    channel_ts: str | None,
) -> None:
    await pool.execute(
        """
        UPDATE birthday_posts
        SET status = 'sent',
            channel_ts = $3,
            error = NULL,
            sent_at = NOW(),
            updated_at = NOW()
        WHERE slack_user_id = $1 AND birthday_date = $2
        """,
        slack_user_id,
        birthday_date,
        channel_ts,
    )


async def mark_birthday_post_failed(
    pool: asyncpg.Pool,
    slack_user_id: str,
    birthday_date: date,
    *,
    error: str,
) -> None:
    await pool.execute(
        """
        UPDATE birthday_posts
        SET status = 'failed',
            error = $3,
            updated_at = NOW()
        WHERE slack_user_id = $1 AND birthday_date = $2
        """,
        slack_user_id,
        birthday_date,
        error[:500],
    )


async def mark_birthday_dm_sent(
    pool: asyncpg.Pool, slack_user_id: str, birthday_date: date
) -> None:
    await pool.execute(
        """
        UPDATE birthday_posts
        SET dm_status = 'sent',
            dm_error = NULL,
            updated_at = NOW()
        WHERE slack_user_id = $1 AND birthday_date = $2
        """,
        slack_user_id,
        birthday_date,
    )


async def mark_birthday_dm_failed(
    pool: asyncpg.Pool,
    slack_user_id: str,
    birthday_date: date,
    *,
    error: str,
) -> None:
    await pool.execute(
        """
        UPDATE birthday_posts
        SET dm_status = 'failed',
            dm_error = $3,
            updated_at = NOW()
        WHERE slack_user_id = $1 AND birthday_date = $2
        """,
        slack_user_id,
        birthday_date,
        error[:500],
    )


@asynccontextmanager
async def birthday_send_lock(
    pool: asyncpg.Pool, slack_user_id: str, birthday_date: date
):
    lock_key = birthday_date.isoformat()
    async with pool.acquire() as conn:
        locked = await conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtext($1), hashtext($2))",
            slack_user_id,
            lock_key,
        )
        if not locked:
            yield False
            return

        try:
            yield True
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock(hashtext($1), hashtext($2))",
                slack_user_id,
                lock_key,
            )


async def set_receive_wishes(
    pool: asyncpg.Pool, slack_user_id: str, receive_wishes: bool
) -> None:
    await pool.execute(
        """
        INSERT INTO user_preferences (slack_user_id, receive_wishes, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (slack_user_id) DO UPDATE
        SET receive_wishes = EXCLUDED.receive_wishes,
            updated_at = NOW()
        """,
        slack_user_id,
        receive_wishes,
    )


async def get_receive_wishes(pool: asyncpg.Pool, slack_user_id: str) -> bool:
    value = await pool.fetchval(
        """
        SELECT receive_wishes
        FROM user_preferences
        WHERE slack_user_id = $1
        """,
        slack_user_id,
    )
    return True if value is None else bool(value)


async def get_bot_state(pool: asyncpg.Pool, key: str) -> str | None:
    return await pool.fetchval(
        """
        SELECT value
        FROM bot_state
        WHERE key = $1
        """,
        key,
    )


async def set_bot_state(pool: asyncpg.Pool, key: str, value: str) -> None:
    await pool.execute(
        """
        INSERT INTO bot_state (key, value, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value,
            updated_at = NOW()
        """,
        key,
        value,
    )


async def delete_bot_state(pool: asyncpg.Pool, key: str) -> None:
    await pool.execute(
        """
        DELETE FROM bot_state
        WHERE key = $1
        """,
        key,
    )
