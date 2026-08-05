BEGIN;

ALTER TABLE salons
    ADD COLUMN IF NOT EXISTS paddle_subscription_updated_at TIMESTAMPTZ;

COMMIT;
