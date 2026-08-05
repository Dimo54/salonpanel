# Paddle rollout

## Feature overview

SalonPanel provides a seven-day local trial, Paddle.js checkout, signed and idempotent subscription webhooks, central application access control, a persisted past-due grace deadline, a local subscription status page, and server-created Paddle Customer Portal sessions. Page loads never call Paddle; Paddle is queried only while processing a signed supported webhook or creating a portal session.

## Local states and access

Authoritative Paddle statuses map as follows:

| Paddle | Local | Access |
|---|---|---|
| `active` | `active` | allowed |
| `trialing` | `active` | allowed |
| `past_due` | `past_due` | through the persisted grace deadline, inclusive |
| `paused` | `blocked` | denied |
| `canceled` | `cancelled` | denied |
| other/missing | `blocked` | denied |

Local `trial` access requires a valid `trial_ends_at` on or after the current local date. Missing, malformed, or expired dates deny access. Unknown, empty, null, `blocked`, and `cancelled` states deny access. Super administrators, including while impersonating, retain access.

Registration persists a seven-day trial. On the first authoritative transition to `past_due`, the webhook persists `subscription_grace_ends_at = authoritative updated_at + SUBSCRIPTION_GRACE_DAYS`. Subsequent `past_due` updates preserve an existing deadline. Active, blocked, paused, canceled, and unknown states clear it. If `updated_at` is invalid, the signed event's valid `occurred_at` is the deterministic fallback; if both are invalid, processing rolls back and grants no access.

## Configuration

Required for Paddle:

```text
PADDLE_ENVIRONMENT=sandbox|production
PADDLE_CLIENT_TOKEN=...
PADDLE_API_KEY=...
PADDLE_MONTHLY_PRICE_ID=pri_...
PADDLE_YEARLY_PRICE_ID=pri_...
PADDLE_WEBHOOK_SECRET=...
```

Optional safe defaults:

```text
PADDLE_WEBHOOK_TOLERANCE_SECONDS=5
SUBSCRIPTION_GRACE_DAYS=7
```

`SUBSCRIPTION_GRACE_DAYS` accepts integers from 0 through 365; malformed, empty, negative, or larger values fall back to 7. Never expose `PADDLE_API_KEY` or `PADDLE_WEBHOOK_SECRET` to Paddle.js or templates.

The Paddle API key requires exactly these application permissions:

- `subscription.read` for authoritative `GET /subscriptions/{subscription_id}` reads.
- `customer_portal_session.write` for `POST /customers/{customer_id}/portal-sessions`.

Enable these webhook events on the notification destination:

- `subscription.created`
- `subscription.updated`
- `subscription.canceled`

## Sandbox checklist

1. Create sandbox monthly and yearly recurring prices and record their `pri_...` IDs.
2. Create a sandbox client-side token and an API key with `subscription.read` and `customer_portal_session.write`.
3. Configure the sandbox customer portal actions required for the seller account.
4. Create a notification destination pointing to `https://YOUR-HOST/webhooks/paddle` and enable the three subscription events.
5. Put the destination secret in `PADDLE_WEBHOOK_SECRET`; it is not the API key.
6. Set `PADDLE_ENVIRONMENT=sandbox` and all required variables in the application environment.
7. Apply migrations, deploy to a non-production environment, and run the manual lifecycle below.

## Production checklist

1. Recreate products/prices, client token, API key, portal configuration, and notification destination in live Paddle; sandbox entities do not transfer.
2. Set `PADDLE_ENVIRONMENT=production` and use only live IDs and credentials.
3. Confirm the public HTTPS webhook URL and selected events.
4. Back up PostgreSQL and apply the additive migrations before starting the new application version.
5. Deploy one application version with the synchronized schema and configuration.
6. Run a low-risk live checkout and verify webhook processing before onboarding more salons.

## Database migration order

Back up the database, then run from the repository root:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260805_add_paddle_event_id.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260805_add_paddle_subscription_updated_at.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260805_add_subscription_grace_ends_at.sql
```

All three are additive and idempotent. The application fresh-install schema contains the same columns and unique event index.

## Verification commands

```bash
.venv/bin/python -m unittest tests.test_paddle -v
.venv/bin/python -m unittest tests.test_subscription_access -v
.venv/bin/python -m unittest discover -s tests -v
git diff --check
.venv/bin/python -m compileall -q app.py
```

## Manual sandbox lifecycle

1. Register a new salon and confirm a seven-day local trial and protected application access.
2. Open `/subscription`, select a plan, and complete sandbox checkout.
3. Confirm `subscription.created` is accepted and the local state becomes `active` with the expected plan.
4. Confirm protected pages work and an active account is not offered another checkout.
5. POST the CSRF-protected portal form and confirm it opens the sandbox Paddle customer portal.
6. Simulate `past_due`; confirm the persisted deadline is authoritative update time plus the configured days.
7. Repeat a later `past_due` update and confirm the deadline does not move.
8. Test once inside grace (protected access works) and once after grace (HTML redirects; JSON receives 403).
9. Recover payment to `active`; confirm the deadline clears and protected access returns.
10. Cancel in Paddle; confirm `subscription.canceled` maps to `cancelled`, access is denied, and the portal recovery route remains reachable.
11. Confirm public booking, public availability, reminder task authentication, and the Paddle webhook remain operational throughout.

## Render deployment checklist

1. Take a verified database backup.
2. Apply migrations in the order above using the production database connection.
3. Add or verify all Paddle variables in Render without printing their values.
4. Deploy the reviewed branch through the normal Render workflow; do not run migrations concurrently from multiple instances.
5. Verify startup, landing/login, a protected request, subscription recovery, public booking, and webhook response codes.
6. Inspect sanitized logs and perform the production smoke test.

## Rollback

1. Stop new deployments and restore the previous application release.
2. Leave the additive nullable columns and event index in place; the previous release can ignore them and no destructive down migration is required.
3. If billing access must be restored temporarily, use the existing audited super-admin subscription control according to incident procedure.
4. Preserve webhook event rows and logs for diagnosis; do not replay events until the cause and timestamp ordering are understood.
5. Rotate any credential that might have been exposed.

## Log inspection

- Confirm accepted webhook response codes, duplicate handling, ignored unsupported events, and retryable failures.
- Confirm invalid signatures cause no Paddle API or database work.
- Confirm there are no API keys, notification secrets, customer IDs, portal URLs/tokens, authorization headers, or full Paddle response bodies.
- Confirm portal failures use the generic `Paddle API request failed.` message only.
- Confirm no recurring access redirects or JSON-to-HTML redirects.

Never paste API keys, webhook secrets, signed portal URLs, or authorization headers into screenshots, commits, logs, tickets, support messages, or chat.
