# Render production deployment runbook

This is an operator checklist, not automation. It must not be used until sandbox acceptance and PR approval are complete.

## Go/no-go prerequisites

Go only when all of these are true:

- A verified PostgreSQL/Supabase backup exists, its SHA-256 is recorded, and `pg_restore --list` succeeds.
- Current production commit, service settings, start command, environment names, and current Auto-Deploy policy are recorded without values.
- The PR points to one reviewed commit and all automated/sandbox acceptance checks pass.
- Live Paddle product/prices, client token, least-privilege API key, Customer Portal, notification destination, event subscriptions, and destination secret are separately configured. Sandbox entities are not reused.
- A rollback operator and decision window are assigned.

No-go on a failed/unverified backup, dirty/unreviewed commit, missing configuration, mixed sandbox/live entities, migration check failure, unavailable previous Render artifact, or unresolved security/test failure.

## Manual deployment order

1. Record the exact current production commit and the previous successful Render deploy ID/artifact availability.
2. Take a verified PostgreSQL/Supabase backup before any schema or application change. For this repository helper:

   ```bash
   DATABASE_URL='set-outside-shell-history' scripts/create_postgres_backup.sh \
     --output-dir /explicit/protected/directory --environment production \
     --confirm-production BACKUP-PRODUCTION --verify-structure
   ```

3. Review the PR, test logs, sandbox acceptance record, diff, and security checklist in [PR preparation](PR_PREPARATION.md).
4. In Render Environment, add/verify the documented production variable names without displaying/copying values into logs. Use only live Paddle values and `PADDLE_ENVIRONMENT=production`.
5. Keep Auto-Deploy consistent with the service's existing policy; record it before changing anything. This repository has no manifest policy. Render's dashboard **Deploy a specific commit** disables Auto-Deploy, so explicitly decide when it should be restored to the recorded policy.
6. In the approved safe window, apply the migrations once from a controlled operator host, never concurrently from application instances. Run the read-only `--check --environment production` afterward.
7. In Render **Deploys**, choose **Manual Deploy → Deploy a specific commit**, paste/select the reviewed SHA, and confirm the SHA before starting. Do not deploy “latest” by assumption.
8. Verify build/startup logs show `gunicorn app:app`, successful binding/start, and no secret values or database diagnostics. There is currently no repository health endpoint; do not configure a guessed path.
9. Run the unauthenticated non-destructive HTTP smoke test. Then use an explicitly authenticated test session to verify `/subscription` recovery. Do not perform checkout yet.
10. Confirm landing/public/legal/login/register/protected redirect/security/no-store behavior is correct. If a controlled salon slug is available, check public booking without submission.
11. Only after public and protected checks pass, perform the pre-approved low-risk live billing test with dedicated test business data.
12. Inspect Paddle webhook delivery and sanitized application logs; confirm correct plan/state, idempotency, and no sensitive output.
13. Record production acceptance and either restore Auto-Deploy to the previously recorded policy or document the approved new policy.

Render officially documents [specific-commit deployment and its Auto-Deploy effect](https://render.com/docs/deploys), [dashboard rollback behavior](https://render.com/docs/rollbacks), and [logical backups/point-in-time recovery](https://render.com/docs/postgresql-backups).

## Rollback triggers

Rollback immediately for startup failure, repeated 5xx responses, unsafe redirect/canonical behavior, missing critical security headers, protected/public access regression, webhook signature bypass, systematic webhook failure, wrong subscription/plan mapping, duplicate mutation, grace extension, secret/personal-data exposure, or database errors caused by the release. Pause billing acceptance while investigating.

Follow [the exact rollback order](ROLLBACK_RUNBOOK.md). Additive columns normally remain during application rollback; schema rollback requires a separate reviewed plan and is not part of this release.
