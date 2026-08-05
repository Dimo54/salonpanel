#!/usr/bin/env bash
set -euo pipefail

output_directory=""
target_environment=""
verify_structure=0
production_confirmation=""

usage() {
    echo "Usage: scripts/create_postgres_backup.sh --output-dir DIR --environment sandbox|production [--verify-structure] [--confirm-production BACKUP-PRODUCTION]"
}

while (($#)); do
    case "$1" in
        --output-dir)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            output_directory="$2"
            shift 2
            ;;
        --environment)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            target_environment="$2"
            shift 2
            ;;
        --verify-structure)
            verify_structure=1
            shift
            ;;
        --confirm-production)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            production_confirmation="$2"
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

[[ -n "${DATABASE_URL:-}" ]] || { echo "DATABASE_URL: missing" >&2; exit 2; }
[[ -n "$output_directory" ]] || { echo "Output directory: missing" >&2; exit 2; }
[[ "$target_environment" == "sandbox" || "$target_environment" == "production" ]] || {
    echo "Environment must be sandbox or production." >&2
    exit 2
}
if [[ "$target_environment" == "production" && "$production_confirmation" != "BACKUP-PRODUCTION" ]]; then
    echo "Production backup requires the documented explicit confirmation." >&2
    exit 2
fi
command -v pg_dump >/dev/null || { echo "pg_dump: missing" >&2; exit 2; }
command -v sha256sum >/dev/null || { echo "sha256sum: missing" >&2; exit 2; }
if ((verify_structure)); then
    command -v pg_restore >/dev/null || { echo "pg_restore: missing" >&2; exit 2; }
fi

mkdir -p -- "$output_directory"
[[ -d "$output_directory" && -w "$output_directory" ]] || { echo "Output directory is not writable." >&2; exit 2; }

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$output_directory/salonpanel_${target_environment}_${timestamp}.dump"
[[ ! -e "$backup_file" ]] || { echo "Backup destination already exists." >&2; exit 1; }
temporary_file="$(mktemp "$output_directory/.salonpanel_backup.XXXXXX")"
cleanup_temporary_file() {
    rm -f -- "$temporary_file"
}
trap cleanup_temporary_file EXIT

echo "Creating timestamped PostgreSQL custom-format backup..."
if ! pg_dump --format=custom --file="$temporary_file" --no-password "$DATABASE_URL" 2>/dev/null; then
    echo "Backup failed; connection details were suppressed." >&2
    exit 1
fi
[[ -s "$temporary_file" ]] || { echo "Backup failed: output file is empty." >&2; exit 1; }

if ((verify_structure)); then
    pg_restore --list "$temporary_file" >/dev/null
    echo "Structural verification: PASS"
fi
mv -- "$temporary_file" "$backup_file"
trap - EXIT
echo "Backup file: $backup_file"
echo "SHA-256: $(sha256sum "$backup_file" | awk '{print $1}')"
