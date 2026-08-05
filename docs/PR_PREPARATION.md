# GitHub PR preparation

## Recommended title

`Prepare safe Paddle sandbox rollout and production runbooks`

## Recommended PR body

```markdown
## Summary

- add a fixed-order, dry-run-first Paddle PostgreSQL migration runner with schema check mode and explicit production confirmation
- add value-redacting sandbox/production environment validation
- add a clean-tree local release gate covering Paddle, subscription access, full tests, diff checks, compilation, migrations, and documented configuration
- add a non-destructive deployed HTTP smoke test with HTTPS URL validation, invalid-signature webhook checks, redirects, security headers, and no-store checks
- add an explicit-output PostgreSQL custom-format backup helper with SHA-256 and optional structural verification
- document the controlled Paddle sandbox lifecycle, Render production order, acceptance gates, and application-first rollback
- standardize new deployments on `REMINDER_SECRET` while retaining the legacy `CRON_SECRET` fallback for rotation safety

## Scope and safety

This PR does not deploy, change Render or Paddle resources, submit a payment, migrate a database, or alter production data. Tool defaults are non-destructive. Database changes require explicit modes; production apply/backup requires an additional confirmation phrase. No credentials or personal data are included.

## Migrations

Apply once, in this fixed order:

1. `20260805_add_paddle_event_id.sql`
2. `20260805_add_paddle_subscription_updated_at.sql`
3. `20260805_add_subscription_grace_ends_at.sql`

All are additive and rerunnable. Verify afterward with migration `--check`. Application rollback normally leaves these nullable columns and the unique event index in place.

## Verification

- [ ] `scripts/validate_release.sh`
- [ ] `.venv/bin/python -m unittest discover -s tests -v`
- [ ] `git diff --check`
- [ ] `.venv/bin/python -m compileall -q app.py`
- [ ] `bash -n scripts/validate_release.sh`
- [ ] `bash -n scripts/create_postgres_backup.sh`

## Rollout

Follow `docs/PADDLE_ROLLOUT.md`, complete `docs/SANDBOX_SMOKE_TEST.md`, and use `docs/DEPLOYMENT_RUNBOOK.md` only after approval. Roll back in the exact order in `docs/ROLLBACK_RUNBOOK.md`.
```

## Reviewer checklist

- [ ] Scope contains tooling, compatibility-safe reminder configuration, tests, and runbooks only.
- [ ] No application billing/access behavior changed unintentionally.
- [ ] Commands use argument arrays/no shell evaluation for operator input.
- [ ] Defaults do not deploy, submit payment, or write the database.
- [ ] Failure paths return nonzero and suppress connection/secret details.
- [ ] Official Paddle/Render links support dashboard-specific instructions.

## Migration checklist

- [ ] Exact three-file order is fixed in code and tests.
- [ ] Missing files fail before `psql`.
- [ ] `ON_ERROR_STOP` and `psql -X` are used.
- [ ] Dry-run is the default; check mode is read-only.
- [ ] Apply is explicit; production apply requires the documented phrase.
- [ ] Post-apply schema check verifies all three columns and unique event index.
- [ ] Backup and single-operator migration window are approved.

## Security checklist

- [ ] No `.env`, credentials, hashes/fragments of credentials, cookies, personal data, or signed portal URLs are added.
- [ ] Environment and HTTP tooling never prints values, bodies, or authorization headers.
- [ ] Base URLs require HTTPS; local HTTP is explicitly opted in.
- [ ] Redirect destinations remain same-origin.
- [ ] No shell injection, destructive SQL, database reset/drop/truncate, deploy hook, or payment automation exists.
- [ ] Temporary backup file is securely scoped and cleaned only on helper failure.
- [ ] `REMINDER_SECRET` is primary and legacy fallback behavior is tested.

## Sandbox acceptance checklist

- [ ] Sandbox product, monthly/yearly prices, token, least-privilege key, portal, notification destination, events, and webhook secret configured.
- [ ] Environment validation, migration apply/check, release gate, and unauthenticated smoke pass.
- [ ] New test salon receives seven-day trial.
- [ ] Official sandbox checkout activates correct plan and protected access.
- [ ] Duplicate delivery is idempotent.
- [ ] Customer Portal opens through the app.
- [ ] First `past_due` persists grace; repeat does not extend it.
- [ ] Recovery clears grace; cancellation restricts protected access but keeps recovery.
- [ ] Public booking remains reachable and logs are sanitized.

## Production acceptance checklist

- [ ] Verified backup plus SHA-256 and structural listing recorded.
- [ ] Previous production SHA/deploy and Auto-Deploy policy recorded.
- [ ] Live-only Paddle resources and protected environment names verified.
- [ ] Fixed migrations applied once and check mode passes.
- [ ] Reviewed SHA deployed; startup and non-destructive smoke pass.
- [ ] Controlled billing test performed only after route checks.
- [ ] Webhook delivery, state/plan, idempotency, portal, access, and sanitized logs accepted.
- [ ] Final Auto-Deploy policy explicitly recorded.

## Rollback checklist

- [ ] Stop rollout/billing tests and preserve evidence.
- [ ] Roll back to the recorded previous successful Render deploy.
- [ ] Keep dashboard-disabled Auto-Deploy off during diagnosis.
- [ ] Run public, protected recovery, and booking smoke checks.
- [ ] Preserve event rows/deliveries; do not replay blindly.
- [ ] Leave additive Paddle columns/index in place.
- [ ] Restore a database only under a separately approved corruption-recovery plan.
