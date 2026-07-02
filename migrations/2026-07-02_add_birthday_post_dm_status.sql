ALTER TABLE birthday_posts
    ADD COLUMN IF NOT EXISTS dm_status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS dm_error TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'birthday_posts_dm_status_check'
    ) THEN
        ALTER TABLE birthday_posts
            ADD CONSTRAINT birthday_posts_dm_status_check
            CHECK (dm_status IN ('sent', 'failed'));
    END IF;
END $$;
