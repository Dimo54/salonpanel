# Application rollback runbook

## Exact order

1. Declare no-go, stop further rollout work, pause controlled billing tests, and preserve timestamps/deploy IDs. Do not delete webhook deliveries or event rows.
2. Record the failing deploy SHA and current sanitized symptoms. If credential exposure is suspected, restrict access and start credential rotation through the owning dashboards.
3. In Render **Deploys**, select the recorded previous successful deploy and choose **Rollback to this deploy**. Confirm the target SHA/deploy before executing.
4. Render dashboard rollback automatically disables Auto-Deploy. Leave it disabled while diagnosing so the bad latest commit is not automatically reintroduced.
5. Verify startup logs, then run the default non-destructive HTTP smoke test against the restored revision.
6. Verify protected access and subscription recovery with the controlled authenticated test account; confirm public booking still works without submission.
7. Inspect Paddle delivery and sanitized application logs. Preserve failed/duplicate events; do not replay until event ordering, idempotency, and the failure cause are understood.
8. For a reliable-reminder rollback, stop the new reminder cron before restoring code. Leave `appointments.schedule_version`, `schedule_started_at`, and `reminder_events` in place. Successful new reminders are dual-written to legacy `email_events`, so the old code retains its deduplication evidence.
9. Re-enable only the restored revision's cron after checking the legacy reminder evidence and sanitized logs. Never run old and new reminder cron workers concurrently.
10. Brevo has no confirmed exactly-once delivery contract here: a process may stop after Brevo accepts a message and before the database commit. Preserve the processing/event rows; investigate before replaying or manually retrying any reminder.
11. Old code does not increment `schedule_version` when it reschedules an appointment. After rollback, such a moved appointment can miss a reminder on a later reliable-reminder redeploy; record the affected appointments and avoid treating this rollback interval as complete schedule-version history.
12. Leave `paddle_event_id`, `paddle_subscription_updated_at`, `subscription_grace_ends_at`, and the unique event index in place. They are additive/nullable and older application code can ignore them.
13. Restore a database only for demonstrated data corruption and only under a separately approved recovery plan. Never run an improvised down migration, reset, drop, delete, or truncate.
14. Fix/review/test a new commit, then restore Auto-Deploy to the policy recorded before rollout only when the incident owner approves.

## Historical reminder timezone decision (before migration)

Do not infer the convention from a naive value. In the approved maintenance
window, before any write, collect the following read-only evidence and attach it
to the rollout record:

```sql
SELECT data_type, datetime_precision
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'appointments'
  AND column_name = 'created_at';

SHOW TimeZone;

SELECT id, created_at, date, time, status
FROM appointments
WHERE created_at IS NOT NULL
ORDER BY created_at
LIMIT 20;
```

For several appointments whose creation time is independently known (booking
audit, sanitized application log, or operator record), compare the known UTC
instant and the known Europe/Belgrade wall-clock time with `created_at`. Review
the historical Render process configuration and deploy logs for `APP_TIMEZONE`,
runtime timezone, and application version. A `timestamp with time zone` can be
rendered in the approved session zone; a naive `timestamp` requires this
evidence to select either `utc` or `local-wall-clock`. Record the samples,
source, session timezone, Render configuration, chosen convention, and reviewer
before supplying `--history-timestamp-convention`. These SQL statements are
read-only; do not run a migration until the conclusion is documented.

Rollback is complete only when the prior revision is confirmed live, smoke tests pass, billing changes are paused or safe, logs contain no sensitive data, and the incident record includes the deployed SHA and remaining follow-up.

Render notes that dashboard rollback reuses the selected deploy artifact and disables Auto-Deploy; current service configuration and stateful resources have specific exceptions. Review the current [Render rollback documentation](https://render.com/docs/rollbacks) before executing.
