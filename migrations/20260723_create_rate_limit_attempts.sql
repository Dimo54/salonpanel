BEGIN;

CREATE TABLE IF NOT EXISTS rate_limit_attempts (
    id BIGSERIAL PRIMARY KEY,
    scope TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_attempts_lookup
    ON rate_limit_attempts(scope, key_hash, created_at);

CREATE INDEX IF NOT EXISTS idx_rate_limit_attempts_created_at
    ON rate_limit_attempts(created_at);

COMMIT;
