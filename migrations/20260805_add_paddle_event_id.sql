BEGIN;

ALTER TABLE subscription_events
    ADD COLUMN IF NOT EXISTS paddle_event_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_events_paddle_event_id
    ON subscription_events(provider, paddle_event_id)
    WHERE paddle_event_id IS NOT NULL;

COMMIT;
