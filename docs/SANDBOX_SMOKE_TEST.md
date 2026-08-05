# Paddle sandbox lifecycle and smoke test

Labels: **[A]** automated, **[M]** manual, **[D]** Paddle dashboard access, **[Δ]** changes sandbox data. Run in this exact order.

## Before deployment

1. **[M][D][Δ]** In Paddle sandbox, create the product and monthly/yearly recurring prices. Record the two price IDs in the protected environment store.
2. **[M][D][Δ]** Create a sandbox client-side token.
3. **[M][D][Δ]** Create a least-privilege API key with `subscription.read` and `customer_portal_session.write`.
4. **[M][D][Δ]** Enable/configure Customer Portal actions required for subscription recovery.
5. **[M][D][Δ]** Create a URL notification destination at `https://SANDBOX-HOST/webhooks/paddle`.
6. **[M][D][Δ]** Subscribe it to `subscription.created`, `subscription.updated`, and `subscription.canceled`.
7. **[M][D]** Copy that destination's webhook secret to the protected environment store. It is not the API key.
8. **[M][Δ]** Set every environment name listed in [the rollout control document](PADDLE_ROLLOUT.md#required-environment-variable-names), with `PADDLE_ENVIRONMENT=sandbox` and sandbox-only entities.
9. **[A]** Run `scripts/validate_release.sh` in a clean reviewed checkout.
10. **[A]** Run `.venv/bin/python scripts/validate_environment.py --mode sandbox` in the deployment environment without capturing values in logs.
11. **[A][Δ]** Take a sandbox backup if the database contains data, then apply the three migrations with `--apply --environment sandbox`; run `--check` afterward.
12. **[M][Δ]** Deploy the reviewed commit to a non-production/sandbox service where possible. Do not point it at a production database.
13. **[A]** Run the unauthenticated HTTP smoke test below. Stop if any critical check fails.

## Manual lifecycle acceptance

14. **[M][Δ]** Register a new test salon; do not use real personal data.
15. **[M]** Confirm the new salon has exactly the configured seven-day local trial and protected access.
16. **[M][Δ]** Open `/subscription`, choose monthly, and complete checkout with Paddle's official sandbox process and published sandbox test payment details. Do not automate payment submission.
17. **[M][D]** Confirm Paddle reports successful webhook delivery.
18. **[M]** Confirm local status is `active`, local plan is `monthly`, and the authoritative subscription/customer identifiers are associated with the test salon.
19. **[M]** Confirm protected routes work.
20. **[M][D][Δ]** Redeliver the same notification (or use Paddle's supported sandbox delivery retry) and confirm one Paddle event ID remains authoritative and state is unchanged.
21. **[M][Δ]** Open Customer Portal through the application and confirm the authenticated sandbox portal opens. Never copy its signed URL.
22. **[M][D][Δ]** Use Paddle's supported sandbox simulation/test controls to produce `past_due`; do not edit the production database.
23. **[M]** Confirm the persisted grace deadline equals the first authoritative transition timestamp plus `SUBSCRIPTION_GRACE_DAYS` and access works within grace.
24. **[M][D][Δ]** Deliver a later `past_due` update and confirm the stored deadline does not move.
25. **[M][D][Δ]** Recover the sandbox subscription to `active`; confirm the grace deadline clears and protected access remains available.
26. **[M][D][Δ]** Cancel the sandbox subscription; confirm local `cancelled`, protected access denied, and `/subscription` plus portal recovery remain reachable.
27. **[M]** With an explicit test salon slug, confirm public booking remains reachable without submitting the form.
28. **[M][D]** Inspect only sanitized application logs and Paddle delivery metadata. Confirm duplicates are recognized, invalid signatures do no work, and no credentials, cookies, customer data, signed portal URLs, authorization headers, or full webhook bodies appear.

## Non-destructive deployed HTTP smoke test

Default execution sends GET requests to public pages and the protected route plus two invalid webhook POSTs. It creates no user, booking, subscription, or database row. `BASE_URL` must be HTTPS; local HTTP requires `--allow-http-localhost`. Optional authenticated recovery runs only when a cookie is deliberately supplied through a named environment variable.

```bash
.venv/bin/python scripts/http_smoke_test.py --base-url https://SANDBOX-HOST
.venv/bin/python scripts/http_smoke_test.py --base-url https://SANDBOX-HOST --salon-slug test-salon
SMOKE_COOKIE='set-outside-shell-history' .venv/bin/python scripts/http_smoke_test.py \
  --base-url https://SANDBOX-HOST --auth-cookie-env SMOKE_COOKIE
```

The script checks landing, robots, sitemap, privacy, terms, login, registration, an optional booking page, the unauthenticated dashboard redirect, invalid/missing Paddle signatures, common security headers, HSTS, and required `no-store`. Override an intentional status only with `--expected-status /path=CODE`. It never prints response bodies, cookies, or request headers.

Go only if every automated check passes and every lifecycle expectation is observed. No-go on unexpected redirects, missing security/no-store headers, webhook acceptance without a valid signature, stale/extended grace, duplicate state mutation, public booking failure, protected access mismatch, or sensitive logging.
