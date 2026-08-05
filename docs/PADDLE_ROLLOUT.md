# Paddle rollout control document

This document is the single entry point for Paddle sandbox acceptance and the later production release. It does not authorize a deployment, payment, dashboard change, or production database operation.

## Release invariants

- Review and deploy one immutable commit. Never mix sandbox and live Paddle entities.
- Back up and verify PostgreSQL before production migrations.
- Apply only the three additive migrations through the repository runner.
- Paddle webhooks are authoritative. Never repair billing state by replaying an event until ordering and idempotency have been reviewed.
- Application rollback normally leaves the additive nullable columns and unique event index in place.
- Never paste credentials, cookies, Paddle portal URLs, authorization headers, customer data, or webhook bodies into logs, tickets, screenshots, or chat.

Related procedures:

- [Sandbox lifecycle and smoke test](SANDBOX_SMOKE_TEST.md)
- [Render deployment runbook](DEPLOYMENT_RUNBOOK.md)
- [Rollback runbook](ROLLBACK_RUNBOOK.md)
- [PR preparation](PR_PREPARATION.md)

## Required environment variable names

The rollout validator treats every name below as required. It reports only `present`, `missing`, or `malformed`; it never reports any part of a value.

```text
DATABASE_URL
SECRET_KEY
BREVO_API_KEY
BREVO_SENDER_EMAIL
REMINDER_SECRET
PADDLE_ENVIRONMENT
PADDLE_CLIENT_TOKEN
PADDLE_API_KEY
PADDLE_MONTHLY_PRICE_ID
PADDLE_YEARLY_PRICE_ID
PADDLE_WEBHOOK_SECRET
PADDLE_WEBHOOK_TOLERANCE_SECONDS
SUBSCRIPTION_GRACE_DAYS
```

`PADDLE_ENVIRONMENT` must exactly match the validator mode (`sandbox` or `production`). `PADDLE_WEBHOOK_TOLERANCE_SECONDS` must be a nonnegative integer, matching the application parser. `SUBSCRIPTION_GRACE_DAYS` must be an integer from 0 through 365. The validator checks PostgreSQL URL structure and sender email syntax. It deliberately checks Paddle tokens, API keys, prices, and secrets only for presence: rejecting future valid values based on guessed formats would be unsafe. Paddle documents client-side tokens as browser-safe and API keys/webhook secrets as server-only credentials.

The application uses `REMINDER_SECRET`. `CRON_SECRET` is a temporary compatibility fallback for existing installations and is not accepted as satisfying rollout validation.

```bash
.venv/bin/python scripts/validate_environment.py --mode sandbox
.venv/bin/python scripts/validate_environment.py --mode production
```

## Migration contract

The only Paddle rollout migrations, in immutable order, are:

1. `20260805_add_paddle_event_id.sql`
2. `20260805_add_paddle_subscription_updated_at.sql`
3. `20260805_add_subscription_grace_ends_at.sql`

They add columns/indexes with `IF NOT EXISTS`; no down migration is part of this rollout. The runner requires `DATABASE_URL`, never displays it, uses `psql -X` and `ON_ERROR_STOP`, and suppresses database diagnostics that could contain connection details.

```bash
# Validate filenames/order only; no connection is made.
DATABASE_URL='set-outside-shell-history' .venv/bin/python scripts/run_paddle_migrations.py

# Read-only schema check.
DATABASE_URL='set-outside-shell-history' .venv/bin/python scripts/run_paddle_migrations.py --check --environment sandbox

# Sandbox apply.
DATABASE_URL='set-outside-shell-history' .venv/bin/python scripts/run_paddle_migrations.py --apply --environment sandbox

# Production apply requires both flags and must be run only in the approved window.
DATABASE_URL='set-outside-shell-history' .venv/bin/python scripts/run_paddle_migrations.py \
  --apply --environment production --confirm-production APPLY-PADDLE-MIGRATIONS
```

Prefer exporting `DATABASE_URL` from a protected operator environment instead of placing it literally in shell history. The placeholders above are not credentials.

## Automated release gate

From a clean checkout of the reviewed branch:

```bash
scripts/validate_release.sh
```

The command checks branch `paddle-integration` (override only with `--expected-branch BRANCH`), a clean tree, commit SHA, migrations, documented environment names, Paddle tests, subscription access tests, the full unit suite, `git diff --check`, and Python compilation. It performs no network request and no database write.

## Current application/deployment facts

- Build command documented by this repository: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- No `render.yaml`, Dockerfile, Procfile, deployment automation, or repository-defined Render policy exists.
- No health/readiness route is currently implemented. Do not configure a guessed Render health path; use startup logs and the non-destructive public smoke suite unless a separately reviewed health route is added.
- PostgreSQL schema initialization still runs at process start unless `SALONPANEL_SKIP_DB_INIT=1`; the explicit migration runner remains the controlled rollout step.
- Existing test convention is Python `unittest` with DB initialization disabled and a non-live placeholder `DATABASE_URL`.

## Authoritative state expectations

| Paddle state | Local state | Protected access |
|---|---|---|
| `active` or `trialing` | `active` | allowed |
| `past_due` | `past_due` | allowed through persisted deadline, inclusive |
| `paused` | `blocked` | denied |
| `canceled` | `cancelled` | denied |
| missing/unknown | `blocked` | denied |

The first authoritative `past_due` transition persists the grace deadline. Repeated `past_due` events must not extend it. Active, blocked, paused, canceled, and unknown states clear it. Public booking, reminder authentication, webhook processing, and subscription recovery remain gate-exempt.

## Official references

- Paddle: [build and test Checkout in sandbox](https://developer.paddle.com/build/checkout/build-branded-inline-checkout/)
- Paddle: [API key permissions](https://developer.paddle.com/api-reference/about/permissions/)
- Paddle: [notification destinations](https://developer.paddle.com/webhooks/about/notification-destinations/)
- Paddle: [webhook signature verification](https://developer.paddle.com/webhooks/about/signature-verification/)
- Paddle: [Customer Portal session](https://developer.paddle.com/api-reference/customer-portals/create-customer-portal-session/)
