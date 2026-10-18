-- Run with a PostgreSQL session whose TimeZone matches APP_TIMEZONE.
-- For a naive historical created_at, the operator MUST set
-- salonpanel.history_timestamp_convention to local-wall-clock, utc, or the
-- narrowly-scoped legacy-past-only mode. Do not guess: old deployments may
-- have used either a UTC server session or local application timestamps.
BEGIN;
SET LOCAL lock_timeout = '5s';

-- Block appointment and email-event writes while the historical safety check
-- and legacy backfill run. The rollout still requires a booking/cron pause
-- until the new code is deployed; this lock only closes the in-transaction gap.
LOCK TABLE appointments IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE email_events IN SHARE ROW EXCLUSIVE MODE;

-- Do not use NOT NULL DEFAULT CURRENT_TIMESTAMP here: that would assign the
-- migration instant to historical appointments and manufacture false 24h jobs.
ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS schedule_version INTEGER,
    ADD COLUMN IF NOT EXISTS schedule_started_at TIMESTAMP;

-- Preserve the stored wall-clock value for timestamp columns. If created_at
-- is timestamptz, explicitly render it in the session timezone first.
DO $$
DECLARE created_at_type text;
DECLARE history_convention text;
DECLARE rows_to_backfill bigint;
BEGIN
    SELECT data_type INTO created_at_type
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'appointments'
      AND column_name = 'created_at';

    SELECT current_setting('salonpanel.history_timestamp_convention', true)
      INTO history_convention;
    SELECT count(*) INTO rows_to_backfill
      FROM appointments WHERE schedule_started_at IS NULL;

    IF rows_to_backfill = 0 THEN
        -- Re-applying after a completed migration must remain idempotent even
        -- after new appointments exist.
        NULL;
    ELSIF history_convention = 'legacy-past-only' THEN
        -- This mode deliberately makes no claim about created_at. It is safe
        -- only when every existing appointment is strictly before CURRENT_DATE
        -- in the runner-controlled APP_TIMEZONE session.
        IF EXISTS (SELECT 1 FROM appointments WHERE date IS NULL OR date >= CURRENT_DATE) THEN
            RAISE EXCEPTION 'legacy-past-only requires every appointment date to be before CURRENT_DATE';
        END IF;
        -- A date-at-midnight value is a neutral legacy schedule anchor, not an
        -- inferred creation instant. Past appointments are never reminder due;
        -- any later reschedule receives a new local schedule_started_at/version
        -- from the deployed application.
        UPDATE appointments
        SET schedule_started_at = COALESCE(schedule_started_at, date::timestamp)
        WHERE schedule_started_at IS NULL;
    ELSIF created_at_type = 'timestamp with time zone' THEN
        EXECUTE 'UPDATE appointments
                 SET schedule_started_at = COALESCE(schedule_started_at,
                     created_at AT TIME ZONE current_setting(''TimeZone''))
                 WHERE schedule_started_at IS NULL';
    ELSIF history_convention = 'local-wall-clock' THEN
        UPDATE appointments
        SET schedule_started_at = COALESCE(schedule_started_at, created_at, CURRENT_TIMESTAMP)
        WHERE schedule_started_at IS NULL;
    ELSIF history_convention = 'utc' THEN
        UPDATE appointments
        SET schedule_started_at = COALESCE(schedule_started_at,
            created_at AT TIME ZONE 'UTC' AT TIME ZONE current_setting('TimeZone'))
        WHERE schedule_started_at IS NULL;
    ELSE
        RAISE EXCEPTION 'historical timestamp convention must be local-wall-clock or utc before reminder backfill';
    END IF;
END $$;

UPDATE appointments SET schedule_version = COALESCE(schedule_version, 1)
WHERE schedule_version IS NULL;

ALTER TABLE appointments
    ALTER COLUMN schedule_version SET DEFAULT 1,
    ALTER COLUMN schedule_version SET NOT NULL,
    ALTER COLUMN schedule_started_at SET DEFAULT CURRENT_TIMESTAMP,
    ALTER COLUMN schedule_started_at SET NOT NULL;

CREATE TABLE IF NOT EXISTS reminder_events (
    id BIGSERIAL PRIMARY KEY,
    salon_id INTEGER NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
    appointment_id INTEGER NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
    reminder_type TEXT NOT NULL CHECK (reminder_type IN ('reminder_24h', 'reminder_2h')),
    schedule_version INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('processing', 'successful', 'failed', 'skipped')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_expires_at TIMESTAMP,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    UNIQUE (appointment_id, reminder_type, schedule_version)
);

CREATE INDEX IF NOT EXISTS idx_reminder_events_retry
    ON reminder_events(state, lease_expires_at);

-- Legacy events do not have a schedule version, so they are evidence only for
-- v1. Join appointments to ignore orphan/NULL appointment_id/salon_id rows and
-- take the authoritative salon id from the appointment.
INSERT INTO reminder_events
    (salon_id, appointment_id, reminder_type, schedule_version, state,
     attempt_count, last_error, created_at, updated_at, sent_at)
SELECT a.salon_id, e.appointment_id,
       CASE e.event_key WHEN 'reminder-24h' THEN 'reminder_24h' ELSE 'reminder_2h' END,
       1, 'successful', 1, '', e.created_at, e.created_at, e.created_at
FROM email_events e
JOIN appointments a ON a.id = e.appointment_id
WHERE e.successful = TRUE
  AND e.event_key IN ('reminder-24h', 'reminder-2h')
  AND e.appointment_id IS NOT NULL
  AND a.salon_id IS NOT NULL
ON CONFLICT (appointment_id, reminder_type, schedule_version) DO NOTHING;

-- `legacy-past-only` deliberately avoids inferring historical created_at
-- semantics. Once its transaction commits, an unmodified old application
-- could otherwise create a future appointment before the freeze-aware code is
-- live. Keep a database-enforced gate in place: only a freeze-aware process
-- explicitly setting this session GUC may mutate appointments. This also
-- protects the Render old/new overlap; it is not an authorization mechanism.
DO $$
BEGIN
    IF current_setting('salonpanel.history_timestamp_convention', true) = 'legacy-past-only' THEN
        CREATE OR REPLACE FUNCTION public.salonpanel_enforce_appointment_write_freeze()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        BEGIN
            IF current_setting('salonpanel.appointment_write_freeze_bypass', true) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION 'appointment writes are frozen pending the freeze-aware deployment'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $function$;

        DROP TRIGGER IF EXISTS salonpanel_appointment_write_freeze ON public.appointments;
        CREATE TRIGGER salonpanel_appointment_write_freeze
        BEFORE INSERT OR UPDATE OR DELETE ON public.appointments
        FOR EACH ROW EXECUTE FUNCTION public.salonpanel_enforce_appointment_write_freeze();
    END IF;
END $$;

COMMIT;
