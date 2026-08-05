#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
expected_branch="paddle-integration"

usage() {
    echo "Usage: scripts/validate_release.sh [--expected-branch BRANCH]"
}

while (($#)); do
    case "$1" in
        --expected-branch)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            expected_branch="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

cd "$repository_root"

actual_branch="$(git branch --show-current)"
if [[ "$actual_branch" != "$expected_branch" ]]; then
    echo "FAIL: expected branch '$expected_branch'; current branch differs." >&2
    exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "FAIL: working tree is not clean." >&2
    exit 1
fi

echo "Branch: $actual_branch"
echo "Commit: $(git rev-parse HEAD)"

required_migrations=(
    migrations/20260805_add_paddle_event_id.sql
    migrations/20260805_add_paddle_subscription_updated_at.sql
    migrations/20260805_add_subscription_grace_ends_at.sql
)
for migration in "${required_migrations[@]}"; do
    [[ -f "$migration" ]] || { echo "FAIL: missing migration ${migration##*/}" >&2; exit 1; }
done

required_environment_names=(
    DATABASE_URL SECRET_KEY BREVO_API_KEY BREVO_SENDER_EMAIL REMINDER_SECRET
    PADDLE_ENVIRONMENT PADDLE_CLIENT_TOKEN PADDLE_API_KEY
    PADDLE_MONTHLY_PRICE_ID PADDLE_YEARLY_PRICE_ID PADDLE_WEBHOOK_SECRET
    PADDLE_WEBHOOK_TOLERANCE_SECONDS SUBSCRIPTION_GRACE_DAYS
)
for variable_name in "${required_environment_names[@]}"; do
    rg -q --fixed-strings "$variable_name" docs/PADDLE_ROLLOUT.md || {
        echo "FAIL: required environment name is not documented: $variable_name" >&2
        exit 1
    }
done

test_environment=(
    SALONPANEL_SKIP_DB_INIT=1
    SECRET_KEY=test-secret-key-for-release-validation
    DATABASE_URL=postgresql://test:test@localhost/test
)

echo "Running Paddle tests..."
env "${test_environment[@]}" .venv/bin/python -m unittest tests.test_paddle -v
echo "Running subscription access tests..."
env "${test_environment[@]}" .venv/bin/python -m unittest tests.test_subscription_access -v
echo "Running full unittest suite..."
env "${test_environment[@]}" .venv/bin/python -m unittest discover -s tests -v
echo "Running git diff check..."
git diff --check
echo "Running Python compile validation..."
.venv/bin/python -m compileall -q app.py scripts tests
echo "Release validation: PASS"
