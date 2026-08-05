import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-not-used-outside-tests")
os.environ["SALONPANEL_SKIP_DB_INIT"] = "1"

import app as salon_app


class DeletionCursor:
    def __init__(self, connection):
        self.connection = connection
        self.current_one = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        self.connection.statements.append((sql, params))
        salon_id = params[0]
        if sql.startswith("SELECT id, name FROM salons"):
            self.current_one = next(
                (row.copy() for row in self.connection.tables["salons"] if row["id"] == salon_id),
                None,
            )
        elif sql.startswith("DELETE FROM appointments"):
            deleted_ids = {
                row["id"] for row in self.connection.tables["appointments"] if row["salon_id"] == salon_id
            }
            self.connection.tables["appointments"] = [
                row for row in self.connection.tables["appointments"] if row["salon_id"] != salon_id
            ]
            self.connection.tables["email_events"] = [
                row for row in self.connection.tables["email_events"] if row.get("appointment_id") not in deleted_ids
            ]
        elif sql.startswith("DELETE FROM salons"):
            self.connection.cascade_salon(salon_id)

    def fetchone(self):
        return self.current_one


class DeletionConnection:
    direct_tables = (
        "users",
        "clients",
        "services",
        "workers",
        "worker_time_off",
        "subscription_events",
        "salon_working_hours",
        "booking_attempts",
        "email_events",
    )

    def __init__(self):
        self.tables = {
            "salons": [{"id": 1, "name": "Salon One"}, {"id": 2, "name": "Salon Two"}],
            "users": [{"id": 11, "salon_id": 1}, {"id": 21, "salon_id": 2}],
            "clients": [{"id": 12, "salon_id": 1}, {"id": 22, "salon_id": 2}],
            "services": [{"id": 13, "salon_id": 1}, {"id": 23, "salon_id": 2}],
            "workers": [{"id": 14, "salon_id": 1}, {"id": 24, "salon_id": 2}],
            "worker_services": [
                {"worker_id": 14, "service_id": 13},
                {"worker_id": 24, "service_id": 23},
            ],
            "worker_time_off": [
                {"id": 15, "salon_id": 1, "worker_id": 14},
                {"id": 25, "salon_id": 2, "worker_id": 24},
            ],
            "appointments": [
                {"id": 16, "salon_id": 1, "client_id": 12, "service_id": 13, "worker_id": 14},
                {"id": 26, "salon_id": 2, "client_id": 22, "service_id": 23, "worker_id": 24},
            ],
            "subscription_events": [{"id": 17, "salon_id": 1}, {"id": 27, "salon_id": 2}],
            "salon_working_hours": [{"salon_id": 1}, {"salon_id": 2}],
            "worker_working_hours": [{"worker_id": 14}, {"worker_id": 24}],
            "worker_breaks": [{"worker_id": 14}, {"worker_id": 24}],
            "booking_attempts": [{"id": 18, "salon_id": 1}, {"id": 28, "salon_id": 2}],
            "email_events": [
                {"id": 19, "salon_id": 1, "appointment_id": 16},
                {"id": 29, "salon_id": 2, "appointment_id": 26},
            ],
        }
        self.original = {name: [row.copy() for row in rows] for name, rows in self.tables.items()}
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return DeletionCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        self.tables = {name: [row.copy() for row in rows] for name, rows in self.original.items()}

    def cascade_salon(self, salon_id):
        worker_ids = {row["id"] for row in self.tables["workers"] if row["salon_id"] == salon_id}
        service_ids = {row["id"] for row in self.tables["services"] if row["salon_id"] == salon_id}
        self.tables["salons"] = [row for row in self.tables["salons"] if row["id"] != salon_id]
        for table in self.direct_tables:
            self.tables[table] = [row for row in self.tables[table] if row.get("salon_id") != salon_id]
        self.tables["worker_services"] = [
            row
            for row in self.tables["worker_services"]
            if row["worker_id"] not in worker_ids and row["service_id"] not in service_ids
        ]
        for table in ("worker_working_hours", "worker_breaks"):
            self.tables[table] = [row for row in self.tables[table] if row["worker_id"] not in worker_ids]


class SalonDeletionTests(unittest.TestCase):
    def setUp(self):
        salon_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = salon_app.app.test_client()

    def csrf_token(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "salon-delete-csrf"
        return "salon-delete-csrf"

    def test_successful_deletion_removes_all_direct_and_indirect_tenant_data(self):
        connection = DeletionConnection()
        with patch.object(salon_app, "get_db", return_value=connection):
            deleted = salon_app.delete_salon_tenant_data(1, "Salon One")

        self.assertEqual(deleted["id"], 1)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        for rows in connection.tables.values():
            self.assertFalse(any(row.get("salon_id") == 1 for row in rows))
        self.assertFalse(any(row["id"] == 1 for row in connection.tables["salons"]))
        self.assertFalse(any(row.get("worker_id") == 14 for row in connection.tables["worker_services"]))
        self.assertFalse(any(row.get("worker_id") == 14 for row in connection.tables["worker_working_hours"]))
        self.assertFalse(any(row.get("worker_id") == 14 for row in connection.tables["worker_breaks"]))

    def test_successful_super_admin_request_deletes_and_redirects(self):
        super_admin = {"id": 1, "salon_id": None, "role": "super_admin", "active": True}
        with (
            patch.object(salon_app, "validated_session_user", return_value=super_admin),
            patch.object(
                salon_app,
                "delete_salon_tenant_data",
                return_value={"id": 1, "name": "Salon One"},
            ) as delete_salon,
        ):
            response = self.client.post(
                "/super-admin/salons/1/delete",
                data={
                    "salon_name_confirmation": "Salon One",
                    "csrf_token": self.csrf_token(),
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/super-admin/salons")
        delete_salon.assert_called_once_with(1, "Salon One")

    def test_deletion_of_salon_with_appointments_deletes_appointments_first(self):
        connection = DeletionConnection()
        with patch.object(salon_app, "get_db", return_value=connection):
            salon_app.delete_salon_tenant_data(1, "Salon One")

        delete_statements = [sql for sql, _ in connection.statements if sql.startswith("DELETE")]
        self.assertEqual(delete_statements[0], "DELETE FROM appointments WHERE salon_id = %s")
        self.assertFalse(any(row["salon_id"] == 1 for row in connection.tables["appointments"]))

    def test_deletion_of_salon_with_clients_deletes_its_clients(self):
        connection = DeletionConnection()
        with patch.object(salon_app, "get_db", return_value=connection):
            salon_app.delete_salon_tenant_data(1, "Salon One")

        self.assertFalse(any(row["salon_id"] == 1 for row in connection.tables["clients"]))

    def test_unauthorized_deletion_attempt_does_not_reach_delete_logic(self):
        owner = {"id": 7, "salon_id": 1, "role": "owner", "active": True}
        with (
            patch.object(salon_app, "validated_session_user", return_value=owner),
            patch.object(salon_app, "delete_salon_tenant_data") as delete_salon,
        ):
            response = self.client.post(
                "/super-admin/salons/1/delete",
                data={
                    "salon_name_confirmation": "Salon One",
                    "csrf_token": self.csrf_token(),
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        delete_salon.assert_not_called()

    def test_deletion_leaves_every_other_salon_row_untouched(self):
        connection = DeletionConnection()
        expected_other_rows = {
            name: [row.copy() for row in rows if row.get("salon_id") == 2 or row.get("id") == 2]
            for name, rows in connection.tables.items()
        }
        with patch.object(salon_app, "get_db", return_value=connection):
            salon_app.delete_salon_tenant_data(1, "Salon One")

        for table, expected in expected_other_rows.items():
            actual = [row for row in connection.tables[table] if row.get("salon_id") == 2 or row.get("id") == 2]
            self.assertEqual(actual, expected, table)
        self.assertIn({"worker_id": 24, "service_id": 23}, connection.tables["worker_services"])
        self.assertIn({"worker_id": 24}, connection.tables["worker_working_hours"])
        self.assertIn({"worker_id": 24}, connection.tables["worker_breaks"])

    def test_incorrect_typed_name_rolls_back_without_deleting(self):
        connection = DeletionConnection()
        with (
            patch.object(salon_app, "get_db", return_value=connection),
            self.assertRaises(salon_app.SalonNameConfirmationError),
        ):
            salon_app.delete_salon_tenant_data(1, "salon one")

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.tables, connection.original)


if __name__ == "__main__":
    unittest.main()
