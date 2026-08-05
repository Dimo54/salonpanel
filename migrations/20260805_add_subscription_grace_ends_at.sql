ALTER TABLE salons
ADD COLUMN IF NOT EXISTS subscription_grace_ends_at TIMESTAMPTZ;
