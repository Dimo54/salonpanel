BEGIN;

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS phone_normalized TEXT;

ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS contact_name TEXT,
    ADD COLUMN IF NOT EXISTS contact_phone TEXT,
    ADD COLUMN IF NOT EXISTS contact_phone_normalized TEXT,
    ADD COLUMN IF NOT EXISTS contact_email TEXT,
    ADD COLUMN IF NOT EXISTS contact_source TEXT,
    ADD COLUMN IF NOT EXISTS privacy_consent_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS marketing_consent BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_clients_salon_phone_normalized
    ON clients(salon_id, phone_normalized);

CREATE INDEX IF NOT EXISTS idx_appointments_client_id
    ON appointments(client_id);

COMMIT;
