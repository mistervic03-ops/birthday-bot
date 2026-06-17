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
    posted_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (slack_user_id, birthday_date)
);

CREATE TABLE IF NOT EXISTS bot_state (
    key             VARCHAR(100) PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
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


async def upsert_birthday(
    pool: asyncpg.Pool,
    *,
    slack_user_id: str,
    birth_month: int,
    birth_day: int,
    email: str | None,
) -> None:
    await pool.execute(
        """
        INSERT INTO birthdays (slack_user_id, birth_month, birth_day, email, is_active, updated_at)
        VALUES ($1, $2, $3, $4, TRUE, NOW())
        ON CONFLICT (slack_user_id) DO UPDATE
        SET birth_month = EXCLUDED.birth_month,
            birth_day = EXCLUDED.birth_day,
            email = EXCLUDED.email,
            is_active = TRUE,
            updated_at = NOW()
        """,
        slack_user_id,
        birth_month,
        birth_day,
        email,
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
        SELECT p.slack_user_id, p.birthday_date, p.posted_at, b.email
        FROM birthday_posts p
        LEFT JOIN birthdays b ON b.slack_user_id = p.slack_user_id
        ORDER BY p.posted_at DESC
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
    status = await pool.execute(
        """
        INSERT INTO birthday_posts (slack_user_id, birthday_date)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        slack_user_id,
        birthday_date,
    )
    return status == "INSERT 0 1"


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
