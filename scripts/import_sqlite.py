import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date, timedelta

TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "14"))


def import_owner_password():
    password = os.environ.get("IMPORT_OWNER_PASSWORD", "")
    if not password.strip():
        raise SystemExit(
            "IMPORT_OWNER_PASSWORD is required. Provide an explicit temporary import credential."
        )
    return password


def usage():
    print(
        "Usage: DATABASE_URL=postgresql://... IMPORT_OWNER_PASSWORD=... "
        "python scripts/import_sqlite.py salonpanel.sqlite3 "
        "'Naziv salona' 'email@salona.com' 'Ime vlasnika'"
    )
    raise SystemExit(1)


def main():
    if len(sys.argv) < 5:
        usage()
    sqlite_path = Path(sys.argv[1])
    salon_name = sys.argv[2]
    owner_email = sys.argv[3].lower()
    owner_name = sys.argv[4]
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    owner_password = import_owner_password()
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")

    from werkzeug.security import generate_password_hash
    import app as salon_app

    with salon_app.app.app_context():
        salon_app.init_db()
        slug = salon_app.unique_slug(salon_name)
        salon_row = salon_app.db_execute(
            """
            INSERT INTO salons (name, slug, owner_name, owner_email, subscription_status, subscription_plan, trial_ends_at)
            VALUES (%s, %s, %s, %s, 'trial', 'trial', %s)
            RETURNING id
            """,
            (salon_name, slug, owner_name, owner_email, date.today() + timedelta(days=TRIAL_DAYS)),
            returning=True,
        )
        salon_id = salon_row["id"]
        salon_app.db_execute(
            """
            INSERT INTO users (salon_id, role, name, email, password_hash, active)
            VALUES (%s, 'owner', %s, %s, %s, TRUE)
            """,
            (salon_id, owner_name, owner_email, generate_password_hash(owner_password)),
        )

        old = sqlite3.connect(sqlite_path)
        old.row_factory = sqlite3.Row

        service_map = {}
        service_duration_map = {}
        for service in old.execute("SELECT * FROM services ORDER BY id"):
            row = salon_app.db_execute(
                """
                INSERT INTO services (salon_id, name, price, duration_minutes, description, active, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    salon_id,
                    service["name"],
                    service["price"],
                    service["duration_minutes"],
                    service["description"],
                    bool(service["active"]),
                    service["created_at"],
                ),
                returning=True,
            )
            service_map[service["id"]] = row["id"]
            service_duration_map[service["id"]] = service["duration_minutes"] or 30

        worker_id = salon_app.ensure_salon_default_worker(salon_id)

        client_map = {}
        for client in old.execute("SELECT * FROM clients ORDER BY id"):
            row = salon_app.db_execute(
                """
                INSERT INTO clients (salon_id, name, phone, email, notes, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (salon_id, client["name"], client["phone"], client["email"], client["notes"], client["created_at"]),
                returning=True,
            )
            client_map[client["id"]] = row["id"]

        for appointment in old.execute("SELECT * FROM appointments ORDER BY id"):
            if appointment["client_id"] not in client_map or appointment["service_id"] not in service_map:
                continue
            salon_app.db_execute(
                """
                INSERT INTO appointments
                (salon_id, client_id, service_id, worker_id, date, time, duration_minutes,
                 price, status, source, notes, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    salon_id,
                    client_map[appointment["client_id"]],
                    service_map[appointment["service_id"]],
                    worker_id,
                    appointment["date"],
                    appointment["time"],
                    service_duration_map[appointment["service_id"]],
                    appointment["price"],
                    appointment["status"],
                    appointment["source"],
                    appointment["notes"],
                    appointment["created_at"],
                    appointment["updated_at"],
                ),
            )
        print(f"Imported SQLite data into salon {salon_name} with slug /s/{slug}/zakazi")
        print(f"Owner email: {owner_email}")


if __name__ == "__main__":
    main()
