import asyncio

import pytest

import db


def run(coro):
    return asyncio.run(coro)


class FakePool:
    def __init__(self):
        self.execute_calls = []
        self.fetchval_calls = []

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "OK"

    async def fetchval(self, sql, *args):
        self.fetchval_calls.append((sql, args))
        return 2


def test_upsert_birthday_stores_manual_source() -> None:
    pool = FakePool()

    run(
        db.upsert_birthday(
            pool,
            slack_user_id="UUSER",
            birth_month=8,
            birth_day=26,
            email=None,
            source="manual",
        )
    )

    sql, args = pool.execute_calls[0]
    assert "source" in sql
    assert "source = EXCLUDED.source" in sql
    assert "is_active = TRUE" in sql
    assert args == ("UUSER", 8, 26, None, "manual")


def test_upsert_birthday_rejects_unknown_source() -> None:
    with pytest.raises(ValueError):
        run(
            db.upsert_birthday(
                FakePool(),
                slack_user_id="UUSER",
                birth_month=8,
                birth_day=26,
                email=None,
                source="unknown",
            )
        )


def test_mark_missing_birthdays_inactive_only_targets_hr_rows() -> None:
    pool = FakePool()

    deactivated = run(db.mark_missing_birthdays_inactive(pool, ["UHR"]))

    sql, args = pool.fetchval_calls[0]
    assert deactivated == 2
    assert "WHERE is_active = TRUE" in sql
    assert "AND source = 'hr'" in sql
    assert "AND NOT (slack_user_id = ANY($1::varchar[]))" in sql
    assert args == (["UHR"],)


def test_birthdays_migration_adds_source_and_restores_manual_rows() -> None:
    assert "source          VARCHAR(20) NOT NULL DEFAULT 'hr'" in db.CREATE_SCHEMA_SQL
    assert "ALTER TABLE birthdays ADD COLUMN source VARCHAR(20)" in db.MIGRATE_SCHEMA_SQL
    assert "WHEN email IS NULL THEN 'manual'" in db.MIGRATE_SCHEMA_SQL
    assert "WHERE source = 'manual'" in db.MIGRATE_SCHEMA_SQL
    assert "AND is_active = FALSE" in db.MIGRATE_SCHEMA_SQL
    assert "birthdays_source_check" in db.MIGRATE_SCHEMA_SQL
