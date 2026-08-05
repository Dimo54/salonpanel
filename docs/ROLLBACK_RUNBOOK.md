# Application rollback runbook

## Exact order

1. Declare no-go, stop further rollout work, pause controlled billing tests, and preserve timestamps/deploy IDs. Do not delete webhook deliveries or event rows.
2. Record the failing deploy SHA and current sanitized symptoms. If credential exposure is suspected, restrict access and start credential rotation through the owning dashboards.
3. In Render **Deploys**, select the recorded previous successful deploy and choose **Rollback to this deploy**. Confirm the target SHA/deploy before executing.
4. Render dashboard rollback automatically disables Auto-Deploy. Leave it disabled while diagnosing so the bad latest commit is not automatically reintroduced.
5. Verify startup logs, then run the default non-destructive HTTP smoke test against the restored revision.
6. Verify protected access and subscription recovery with the controlled authenticated test account; confirm public booking still works without submission.
7. Inspect Paddle delivery and sanitized application logs. Preserve failed/duplicate events; do not replay until event ordering, idempotency, and the failure cause are understood.
8. Leave `paddle_event_id`, `paddle_subscription_updated_at`, `subscription_grace_ends_at`, and the unique event index in place. They are additive/nullable and older application code can ignore them.
9. Restore a database only for demonstrated data corruption and only under a separately approved recovery plan. Never run an improvised down migration, reset, drop, delete, or truncate.
10. Fix/review/test a new commit, then restore Auto-Deploy to the policy recorded before rollout only when the incident owner approves.

Rollback is complete only when the prior revision is confirmed live, smoke tests pass, billing changes are paused or safe, logs contain no sensitive data, and the incident record includes the deployed SHA and remaining follow-up.

Render notes that dashboard rollback reuses the selected deploy artifact and disables Auto-Deploy; current service configuration and stateful resources have specific exceptions. Review the current [Render rollback documentation](https://render.com/docs/rollbacks) before executing.
