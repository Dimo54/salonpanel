import csv
import calendar as calendar_module
import io
import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from flask import (
    Flask,
    Response,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

APP_NAME = "SalonPanel"
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "14"))
MONTHLY_PRICE_EUR = int(os.environ.get("MONTHLY_PRICE_EUR", "10"))
YEARLY_PRICE_EUR = int(os.environ.get("YEARLY_PRICE_EUR", "80"))
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/Belgrade")
BOOKING_SLOT_MINUTES = 10

app = Flask(__name__, instance_relative_config=True)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")

STATUS_LABELS = {
    "pending": "Na cekanju",
    "scheduled": "Zakazano",
    "completed": "Zavrseno",
    "cancelled": "Otkazano",
    "no_show": "Nije dosao",
}

STATUS_CLASSES = {
    "pending": "status-pending",
    "scheduled": "status-scheduled",
    "completed": "status-completed",
    "cancelled": "status-cancelled",
    "no_show": "status-no-show",
}

SUBSCRIPTION_LABELS = {
    "trial": "Probni period",
    "active": "Aktivna",
    "past_due": "Placanje kasni",
    "cancelled": "Otkazana",
    "blocked": "Blokirana",
    "free": "Besplatno",
}

PLAN_LABELS = {
    "trial": "Trial",
    "monthly": "10 EUR / mesec",
    "yearly": "80 EUR / godina",
    "free": "Besplatno",
}

DEFAULT_SALON_SETTINGS = {
    "business_type": "Barber & Beauty Studio",
    "phone": "+381 60 000 0000",
    "whatsapp": "381600000000",
    "instagram": "@salonpanel",
    "address": "Kragujevac, Srbija",
    "working_hours": "Pon-Pet 09:00-20:00, Sub 09:00-16:00",
    "booking_note": "Posaljite zahtev za termin. Salon potvrdjuje termin porukom ili pozivom.",
    "open_time": "09:00",
    "close_time": "20:00",
    "slot_minutes": BOOKING_SLOT_MINUTES,
    "booking_mode": "manual",
}

DEFAULT_SERVICES = [
    ("Musko sisanje", 900, 30, "Klasicno ili moderno sisanje"),
    ("Brada", 600, 20, "Sredjivanje i oblikovanje brade"),
    ("Sisanje + brada", 1400, 45, "Kompletan barber tretman"),
    ("Fen frizura", 1200, 45, "Pranje, feniranje i stilizovanje"),
]


def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError(
            "DATABASE_URL nije podesen. U Render Environment dodaj Supabase/PostgreSQL connection string."
        )
    # Supabase/Postgres deployments should use SSL. If the URL has no sslmode,
    # add sslmode=require so the same URL works reliably on Render.
    parsed = urlparse(value)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "sslmode" not in params:
        params["sslmode"] = "require"
        value = urlunparse(parsed._replace(query=urlencode(params)))
    return value


def get_db():
    if "db" not in g:
        g.db = psycopg.connect(database_url(), row_factory=dict_row)
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def db_query(query, args=None, one=False):
    with get_db().cursor() as cur:
        cur.execute(query, args or [])
        rows = cur.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def db_execute(query, args=None, returning=False):
    with get_db().cursor() as cur:
        cur.execute(query, args or [])
        row = cur.fetchone() if returning else None
    get_db().commit()
    if returning:
        return row
    return None


def db_execute_many(query, rows):
    with get_db().cursor() as cur:
        cur.executemany(query, rows)
    get_db().commit()


def local_now():
    try:
        return datetime.now(ZoneInfo(APP_TIMEZONE))
    except Exception:
        return datetime.now()


def local_today():
    return local_now().date()


def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS salons (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE,
        business_type TEXT NOT NULL DEFAULT 'Salon',
        phone TEXT DEFAULT '',
        whatsapp TEXT DEFAULT '',
        instagram TEXT DEFAULT '',
        address TEXT DEFAULT '',
        working_hours TEXT DEFAULT '',
        booking_note TEXT DEFAULT '',
        open_time TEXT NOT NULL DEFAULT '09:00',
        close_time TEXT NOT NULL DEFAULT '20:00',
        slot_minutes INTEGER NOT NULL DEFAULT 10,
        booking_mode TEXT NOT NULL DEFAULT 'manual',
        owner_name TEXT DEFAULT '',
        owner_email TEXT DEFAULT '',
        subscription_status TEXT NOT NULL DEFAULT 'trial',
        subscription_plan TEXT NOT NULL DEFAULT 'trial',
        trial_ends_at DATE,
        paddle_customer_id TEXT,
        paddle_subscription_id TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        salon_id INTEGER REFERENCES salons(id) ON DELETE CASCADE,
        role TEXT NOT NULL DEFAULT 'owner',
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS clients (
        id SERIAL PRIMARY KEY,
        salon_id INTEGER NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        notes TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS services (
        id SERIAL PRIMARY KEY,
        salon_id INTEGER NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        price NUMERIC(12,2) NOT NULL DEFAULT 0,
        duration_minutes INTEGER NOT NULL DEFAULT 30,
        description TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (salon_id, name)
    );

    CREATE TABLE IF NOT EXISTS workers (
        id SERIAL PRIMARY KEY,
        salon_id INTEGER NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        phone TEXT DEFAULT '',
        email TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        active BOOLEAN NOT NULL DEFAULT TRUE,
        is_default BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS worker_services (
        worker_id INTEGER NOT NULL REFERENCES workers(id) ON DELETE CASCADE,
        service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
        price NUMERIC(12,2) NOT NULL DEFAULT 0,
        duration_minutes INTEGER NOT NULL DEFAULT 30,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (worker_id, service_id)
    );

    CREATE TABLE IF NOT EXISTS worker_time_off (
        id SERIAL PRIMARY KEY,
        salon_id INTEGER NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
        worker_id INTEGER NOT NULL REFERENCES workers(id) ON DELETE CASCADE,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        start_time TIME,
        end_time TIME,
        all_day BOOLEAN NOT NULL DEFAULT TRUE,
        reason TEXT DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (end_date >= start_date)
    );

    CREATE TABLE IF NOT EXISTS appointments (
        id SERIAL PRIMARY KEY,
        salon_id INTEGER NOT NULL REFERENCES salons(id) ON DELETE CASCADE,
        client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
        worker_id INTEGER REFERENCES workers(id) ON DELETE RESTRICT,
        date DATE NOT NULL,
        time TIME NOT NULL,
        duration_minutes INTEGER NOT NULL DEFAULT 30,
        price NUMERIC(12,2) NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'scheduled',
        source TEXT NOT NULL DEFAULT 'admin',
        notes TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS subscription_events (
        id SERIAL PRIMARY KEY,
        salon_id INTEGER REFERENCES salons(id) ON DELETE CASCADE,
        provider TEXT NOT NULL DEFAULT 'manual',
        event_type TEXT NOT NULL,
        payload JSONB,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    ALTER TABLE salons ADD COLUMN IF NOT EXISTS booking_mode TEXT NOT NULL DEFAULT 'manual';
    ALTER TABLE salons ALTER COLUMN slot_minutes SET DEFAULT 10;
    ALTER TABLE workers ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE appointments ADD COLUMN IF NOT EXISTS worker_id INTEGER REFERENCES workers(id) ON DELETE RESTRICT;
    ALTER TABLE appointments ADD COLUMN IF NOT EXISTS duration_minutes INTEGER NOT NULL DEFAULT 30;

    UPDATE salons SET slot_minutes = 10 WHERE slot_minutes IS DISTINCT FROM 10;
    UPDATE appointments a
    SET duration_minutes = s.duration_minutes
    FROM services s
    WHERE a.service_id = s.id AND a.worker_id IS NULL;

    CREATE INDEX IF NOT EXISTS idx_users_salon_id ON users(salon_id);
    CREATE INDEX IF NOT EXISTS idx_clients_salon_id ON clients(salon_id);
    CREATE INDEX IF NOT EXISTS idx_services_salon_id ON services(salon_id);
    CREATE INDEX IF NOT EXISTS idx_workers_salon_id ON workers(salon_id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_workers_one_default ON workers(salon_id) WHERE is_default = TRUE;
    CREATE INDEX IF NOT EXISTS idx_worker_services_service_id ON worker_services(service_id);
    CREATE INDEX IF NOT EXISTS idx_worker_time_off_lookup ON worker_time_off(worker_id, start_date, end_date);
    CREATE INDEX IF NOT EXISTS idx_appointments_salon_date ON appointments(salon_id, date, time);
    CREATE INDEX IF NOT EXISTS idx_appointments_worker_date ON appointments(worker_id, date, time);
    CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status);
    """
    with get_db().cursor() as cur:
        cur.execute(schema)
    get_db().commit()
    ensure_default_workers()
    seed_super_admin()


def seed_super_admin():
    email = os.environ.get("SUPER_ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("SUPER_ADMIN_PASSWORD", "")
    name = os.environ.get("SUPER_ADMIN_NAME", "Vlasnik")
    if not email or not password:
        app.logger.warning("SUPER_ADMIN_EMAIL i SUPER_ADMIN_PASSWORD nisu podeseni. Super admin nalog nije kreiran.")
        return

    existing = db_query("SELECT id FROM users WHERE LOWER(email) = LOWER(%s)", (email,), one=True)
    password_hash = generate_password_hash(password)
    if existing:
        db_execute(
            """
            UPDATE users
            SET role = 'super_admin', salon_id = NULL, password_hash = %s, active = TRUE, name = %s
            WHERE id = %s
            """,
            (password_hash, name, existing["id"]),
        )
    else:
        db_execute(
            """
            INSERT INTO users (salon_id, role, name, email, password_hash, active)
            VALUES (NULL, 'super_admin', %s, %s, %s, TRUE)
            """,
            (name, email, password_hash),
        )


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "salon"


def unique_slug(name: str) -> str:
    base = slugify(name)
    slug = base
    counter = 2
    while db_query("SELECT id FROM salons WHERE slug = %s", (slug,), one=True):
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def create_default_services(salon_id: int):
    now = datetime.now()
    rows = [(salon_id, name, price, duration, description, True, now) for name, price, duration, description in DEFAULT_SERVICES]
    db_execute_many(
        """
        INSERT INTO services (salon_id, name, price, duration_minutes, description, active, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (salon_id, name) DO NOTHING
        """,
        rows,
    )


def ensure_salon_default_worker(salon_id: int):
    worker = db_query(
        "SELECT * FROM workers WHERE salon_id = %s ORDER BY is_default DESC, id ASC LIMIT 1",
        (salon_id,),
        one=True,
    )
    if not worker:
        db_execute(
            """
            INSERT INTO workers (salon_id, name, active, is_default, created_at, updated_at)
            VALUES (%s, 'Glavni radnik', TRUE, TRUE, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (salon_id, datetime.now(), datetime.now()),
        )
        worker = db_query(
            "SELECT * FROM workers WHERE salon_id = %s ORDER BY is_default DESC, id ASC LIMIT 1",
            (salon_id,),
            one=True,
        )

    if not worker:
        return None

    assignment_count = db_query(
        "SELECT COUNT(*) AS total FROM worker_services WHERE worker_id = %s",
        (worker["id"],),
        one=True,
    )["total"]
    if worker.get("is_default") and assignment_count == 0:
        db_execute(
            """
            INSERT INTO worker_services
                (worker_id, service_id, price, duration_minutes, active, created_at, updated_at)
            SELECT %s, s.id, s.price, s.duration_minutes, TRUE, %s, %s
            FROM services s
            WHERE s.salon_id = %s
            ON CONFLICT (worker_id, service_id) DO NOTHING
            """,
            (worker["id"], datetime.now(), datetime.now(), salon_id),
        )

    db_execute(
        "UPDATE appointments SET worker_id = %s WHERE salon_id = %s AND worker_id IS NULL",
        (worker["id"], salon_id),
    )
    return worker["id"]


def ensure_default_workers():
    for salon in db_query("SELECT id FROM salons ORDER BY id"):
        ensure_salon_default_worker(salon["id"])


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db_query("SELECT * FROM users WHERE id = %s AND active = TRUE", (user_id,), one=True)


def current_salon():
    user = current_user()
    if not user:
        return None
    if user["role"] == "super_admin" and session.get("impersonate_salon_id"):
        return db_query("SELECT * FROM salons WHERE id = %s", (session["impersonate_salon_id"],), one=True)
    if not user.get("salon_id"):
        return None
    return db_query("SELECT * FROM salons WHERE id = %s", (user["salon_id"],), one=True)


def salon_settings(salon):
    if not salon:
        settings = dict(DEFAULT_SALON_SETTINGS)
        settings["business_name"] = APP_NAME
        return settings
    return {
        "business_name": salon["name"],
        "business_type": salon["business_type"] or DEFAULT_SALON_SETTINGS["business_type"],
        "phone": salon["phone"] or "",
        "whatsapp": salon["whatsapp"] or "",
        "instagram": salon["instagram"] or "",
        "address": salon["address"] or "",
        "working_hours": salon["working_hours"] or "",
        "booking_note": salon["booking_note"] or DEFAULT_SALON_SETTINGS["booking_note"],
        "open_time": salon["open_time"] or DEFAULT_SALON_SETTINGS["open_time"],
        "close_time": salon["close_time"] or DEFAULT_SALON_SETTINGS["close_time"],
        "slot_minutes": BOOKING_SLOT_MINUTES,
        "booking_mode": salon.get("booking_mode") or DEFAULT_SALON_SETTINGS["booking_mode"],
    }


def subscription_is_allowed(salon) -> bool:
    if not salon:
        return False
    status = salon["subscription_status"]
    if status in ("active", "free"):
        return True
    if status == "trial":
        trial_ends_at = salon.get("trial_ends_at")
        if isinstance(trial_ends_at, str):
            try:
                trial_ends_at = datetime.strptime(trial_ends_at, "%Y-%m-%d").date()
            except ValueError:
                return False
        return bool(trial_ends_at and trial_ends_at >= local_today())
    return False


def subscription_days_left(salon):
    if not salon or salon["subscription_status"] != "trial" or not salon.get("trial_ends_at"):
        return None
    trial_ends_at = salon["trial_ends_at"]
    if isinstance(trial_ends_at, str):
        trial_ends_at = datetime.strptime(trial_ends_at, "%Y-%m-%d").date()
    return (trial_ends_at - local_today()).days


@app.context_processor
def inject_globals():
    user = current_user()
    salon = current_salon()
    return {
        "app_name": APP_NAME,
        "current_user": user,
        "current_salon": salon,
        "settings": salon_settings(salon),
        "status_labels": STATUS_LABELS,
        "subscription_labels": SUBSCRIPTION_LABELS,
        "plan_labels": PLAN_LABELS,
        "subscription_is_allowed": subscription_is_allowed(salon),
        "subscription_days_left": subscription_days_left(salon),
        "today_iso": local_today().isoformat(),
        "monthly_price_eur": MONTHLY_PRICE_EUR,
        "yearly_price_eur": YEARLY_PRICE_EUR,
    }


@app.template_filter("money")
def money(value):
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0
    formatted = f"{amount:,.0f}".replace(",", ".")
    return f"{formatted} RSD"


@app.template_filter("euro")
def euro(value):
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0
    return f"{amount:,.0f} EUR".replace(",", ".")


@app.template_filter("date_sr")
def date_sr(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


@app.template_filter("time_short")
def time_short(value):
    if not value:
        return "-"
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)[:5]


@app.template_filter("status_label")
def status_label(value):
    return STATUS_LABELS.get(value, value or "-")


@app.template_filter("status_class")
def status_class(value):
    return STATUS_CLASSES.get(value, "status-scheduled")


@app.template_filter("subscription_label")
def subscription_label(value):
    return SUBSCRIPTION_LABELS.get(value, value or "-")


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped_view


def salon_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user["role"] == "super_admin" and not session.get("impersonate_salon_id"):
            return redirect(url_for("super_admin_dashboard"))
        if not current_salon():
            flash("Salon nije pronadjen za ovaj nalog.", "error")
            return redirect(url_for("logout"))
        return view(*args, **kwargs)
    return wrapped_view


def subscription_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        salon = current_salon()
        if not subscription_is_allowed(salon):
            flash("Pretplata nije aktivna. Aktivirajte nalog da biste nastavili.", "warning")
            return redirect(url_for("subscription_page"))
        return view(*args, **kwargs)
    return wrapped_view


def super_admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user["role"] != "super_admin":
            flash("Nemate pristup super admin panelu.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped_view


def parse_price(value, fallback=0):
    if value is None or value == "":
        return float(fallback or 0)

    text = re.sub(r"[^0-9,.+-]", "", str(value).strip())
    if not text:
        return float(fallback or 0)

    if "." in text and "," in text:
        if text.rfind(",") > text.rfind("."):
            normalized = text.replace(".", "").replace(",", ".")
        else:
            normalized = text.replace(",", "")
    elif "," in text:
        decimal_digits = len(text.rsplit(",", 1)[1])
        normalized = text.replace(",", ".") if text.count(",") == 1 and decimal_digits in (1, 2) else text.replace(",", "")
    elif "." in text:
        decimal_digits = len(text.rsplit(".", 1)[1])
        normalized = text if text.count(".") == 1 and decimal_digits in (1, 2) else text.replace(".", "")
    else:
        normalized = text

    try:
        return float(normalized)
    except ValueError:
        return float(fallback or 0)


def get_or_create_client(salon_id, name, phone=None, email=None, notes=None):
    name = (name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip()
    notes = (notes or "").strip()

    if phone:
        existing = db_query(
            "SELECT * FROM clients WHERE salon_id = %s AND phone = %s",
            (salon_id, phone),
            one=True,
        )
        if existing:
            if name and existing["name"] != name:
                db_execute(
                    "UPDATE clients SET name = %s, email = COALESCE(NULLIF(%s, ''), email) WHERE id = %s AND salon_id = %s",
                    (name, email, existing["id"], salon_id),
                )
            return existing["id"]

    existing_by_name = db_query(
        "SELECT * FROM clients WHERE salon_id = %s AND LOWER(name) = LOWER(%s)",
        (salon_id, name),
        one=True,
    )
    if existing_by_name:
        return existing_by_name["id"]

    row = db_execute(
        """
        INSERT INTO clients (salon_id, name, phone, email, notes)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (salon_id, name, phone, email, notes),
        returning=True,
    )
    return row["id"]


def query_rows(query, args=None, one=False, cursor=None):
    if cursor is None:
        return db_query(query, args, one=one)
    cursor.execute(query, args or [])
    rows = cursor.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def parse_iso_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def time_to_minutes(value):
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if value is None:
        return None
    text = str(value).strip()
    try:
        hour, minute = text[:5].split(":")
        hour = int(hour)
        minute = int(minute)
    except (ValueError, TypeError):
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def minutes_to_time(value):
    value = int(value)
    return f"{value // 60:02d}:{value % 60:02d}"


def intervals_overlap(start_a, end_a, start_b, end_b):
    return start_a < end_b and end_a > start_b


def worker_service_assignment(salon_id, worker_id, service_id, active_only=True, cursor=None):
    sql = """
        SELECT
            ws.worker_id,
            ws.service_id,
            ws.price AS worker_price,
            ws.duration_minutes AS worker_duration_minutes,
            ws.active AS assignment_active,
            w.name AS worker_name,
            w.active AS worker_active,
            s.name AS service_name,
            s.active AS service_active
        FROM worker_services ws
        JOIN workers w ON w.id = ws.worker_id
        JOIN services s ON s.id = ws.service_id
        WHERE w.salon_id = %s AND s.salon_id = %s AND w.id = %s AND s.id = %s
    """
    params = [salon_id, salon_id, worker_id, service_id]
    if active_only:
        sql += " AND w.active = TRUE AND s.active = TRUE AND ws.active = TRUE"
    return query_rows(sql, params, one=True, cursor=cursor)


def appointment_conflict(
    salon_id,
    worker_id,
    appointment_date,
    appointment_time,
    duration_minutes,
    exclude_id=None,
    cursor=None,
):
    start_minutes = time_to_minutes(appointment_time)
    if start_minutes is None:
        return None
    end_minutes = start_minutes + int(duration_minutes or 0)
    params = [salon_id, worker_id, appointment_date]
    sql = """
        SELECT a.id, a.time, a.duration_minutes, c.name AS client_name
        FROM appointments a
        JOIN clients c ON c.id = a.client_id
        WHERE a.salon_id = %s
          AND a.worker_id = %s
          AND a.date = %s
          AND a.status IN ('pending', 'scheduled')
    """
    if exclude_id:
        sql += " AND a.id != %s"
        params.append(exclude_id)
    for row in query_rows(sql, params, cursor=cursor):
        existing_start = time_to_minutes(row["time"])
        existing_end = existing_start + int(row.get("duration_minutes") or 30)
        if intervals_overlap(start_minutes, end_minutes, existing_start, existing_end):
            return row
    return None


def worker_time_off_conflict(
    salon_id,
    worker_id,
    appointment_date,
    appointment_time,
    duration_minutes,
    cursor=None,
):
    start_minutes = time_to_minutes(appointment_time)
    if start_minutes is None:
        return None
    end_minutes = start_minutes + int(duration_minutes or 0)
    rows = query_rows(
        """
        SELECT *
        FROM worker_time_off
        WHERE salon_id = %s AND worker_id = %s AND start_date <= %s AND end_date >= %s
        ORDER BY start_date, start_time NULLS FIRST
        """,
        (salon_id, worker_id, appointment_date, appointment_date),
        cursor=cursor,
    )
    for row in rows:
        if row["all_day"] or row.get("start_time") is None or row.get("end_time") is None:
            return row
        off_start = time_to_minutes(row["start_time"])
        off_end = time_to_minutes(row["end_time"])
        if intervals_overlap(start_minutes, end_minutes, off_start, off_end):
            return row
    return None


def appointment_availability_error(
    salon,
    worker_id,
    appointment_date,
    appointment_time,
    duration_minutes,
    exclude_id=None,
    public_request=False,
    cursor=None,
    worker=None,
):
    selected_date = parse_iso_date(appointment_date)
    start_minutes = time_to_minutes(appointment_time)
    try:
        duration_minutes = int(duration_minutes)
    except (TypeError, ValueError):
        duration_minutes = 0

    if not selected_date or start_minutes is None or duration_minutes <= 0:
        return "Datum, vreme ili trajanje termina nisu ispravni."
    if start_minutes % BOOKING_SLOT_MINUTES != 0:
        return "Termin mora poceti na punih 10 minuta."

    open_minutes = time_to_minutes(salon.get("open_time"))
    close_minutes = time_to_minutes(salon.get("close_time"))
    end_minutes = start_minutes + duration_minutes
    if open_minutes is None or close_minutes is None or close_minutes <= open_minutes:
        return "Radno vreme salona nije pravilno podeseno."
    if start_minutes < open_minutes or end_minutes > close_minutes:
        return "Izabrana usluga ne staje u radno vreme salona."

    if public_request:
        now = local_now()
        if selected_date < now.date():
            return "Izaberite danasnji ili buduci datum."
        if selected_date == now.date() and start_minutes <= now.hour * 60 + now.minute:
            return "Izabrano vreme je vec proslo."

    if worker is None:
        worker = query_rows(
            "SELECT * FROM workers WHERE id = %s AND salon_id = %s",
            (worker_id, salon["id"]),
            one=True,
            cursor=cursor,
        )
    if not worker:
        return "Izabrani radnik ne postoji."
    if not worker["active"]:
        return "Izabrani radnik trenutno nije dostupan za zakazivanje."

    time_off = worker_time_off_conflict(
        salon["id"],
        worker_id,
        selected_date,
        appointment_time,
        duration_minutes,
        cursor=cursor,
    )
    if time_off:
        return "Radnik ne radi u izabranom periodu."

    conflict = appointment_conflict(
        salon["id"],
        worker_id,
        selected_date,
        appointment_time,
        duration_minutes,
        exclude_id=exclude_id,
        cursor=cursor,
    )
    if conflict:
        return f"Radnik vec ima aktivan termin za klijenta {conflict['client_name']} u tom periodu."
    return None


def available_slots_for_worker(salon, worker_id, service_id, appointment_date):
    selected_date = parse_iso_date(appointment_date)
    if not selected_date:
        return None, "Datum nije ispravan."
    if selected_date < local_today():
        return None, "Izaberite danasnji ili buduci datum."

    assignment = worker_service_assignment(salon["id"], worker_id, service_id, active_only=True)
    if not assignment:
        return None, "Izabrani radnik ne pruza ovu uslugu."

    duration_minutes = int(assignment["worker_duration_minutes"] or 0)
    open_minutes = time_to_minutes(salon["open_time"])
    close_minutes = time_to_minutes(salon["close_time"])
    if open_minutes is None or close_minutes is None or close_minutes <= open_minutes:
        return None, "Radno vreme salona nije pravilno podeseno."

    appointments = db_query(
        """
        SELECT a.time, a.duration_minutes
        FROM appointments a
        WHERE a.salon_id = %s AND a.worker_id = %s AND a.date = %s
          AND a.status IN ('pending', 'scheduled')
        """,
        (salon["id"], worker_id, selected_date),
    )
    absences = db_query(
        """
        SELECT * FROM worker_time_off
        WHERE salon_id = %s AND worker_id = %s AND start_date <= %s AND end_date >= %s
        """,
        (salon["id"], worker_id, selected_date, selected_date),
    )

    appointment_ranges = []
    for row in appointments:
        row_start = time_to_minutes(row["time"])
        appointment_ranges.append((row_start, row_start + int(row.get("duration_minutes") or 30)))

    absence_ranges = []
    all_day_absence = False
    for row in absences:
        if row["all_day"] or row.get("start_time") is None or row.get("end_time") is None:
            all_day_absence = True
            break
        absence_ranges.append((time_to_minutes(row["start_time"]), time_to_minutes(row["end_time"])))

    slots = []
    if not all_day_absence:
        first_slot = ((open_minutes + BOOKING_SLOT_MINUTES - 1) // BOOKING_SLOT_MINUTES) * BOOKING_SLOT_MINUTES
        now = local_now()
        current_minutes = now.hour * 60 + now.minute if selected_date == now.date() else None
        for start_minutes in range(first_slot, close_minutes - duration_minutes + 1, BOOKING_SLOT_MINUTES):
            end_minutes = start_minutes + duration_minutes
            if current_minutes is not None and start_minutes <= current_minutes:
                continue
            if any(intervals_overlap(start_minutes, end_minutes, item[0], item[1]) for item in appointment_ranges):
                continue
            if any(intervals_overlap(start_minutes, end_minutes, item[0], item[1]) for item in absence_ranges):
                continue
            slots.append(minutes_to_time(start_minutes))

    return {
        "slots": slots,
        "price": float(assignment["worker_price"] or 0),
        "duration_minutes": duration_minutes,
        "worker_name": assignment["worker_name"],
        "service_name": assignment["service_name"],
    }, None



def available_dates_for_worker(salon, worker_id, service_id, start_date=None, days=90):
    selected_start = parse_iso_date(start_date) or local_today()
    if selected_start < local_today():
        selected_start = local_today()

    try:
        days = max(1, min(int(days), 180))
    except (TypeError, ValueError):
        days = 90
    selected_end = selected_start + timedelta(days=days - 1)

    assignment = worker_service_assignment(salon["id"], worker_id, service_id, active_only=True)
    if not assignment:
        return None, "Izabrani radnik ne pruza ovu uslugu."

    duration_minutes = int(assignment["worker_duration_minutes"] or 0)
    open_minutes = time_to_minutes(salon["open_time"])
    close_minutes = time_to_minutes(salon["close_time"])
    if open_minutes is None or close_minutes is None or close_minutes <= open_minutes:
        return None, "Radno vreme salona nije pravilno podeseno."
    if duration_minutes <= 0 or duration_minutes > close_minutes - open_minutes:
        return {
            "dates": [],
            "price": float(assignment["worker_price"] or 0),
            "duration_minutes": duration_minutes,
            "worker_name": assignment["worker_name"],
            "service_name": assignment["service_name"],
        }, None

    appointment_rows = db_query(
        """
        SELECT a.date, a.time, a.duration_minutes
        FROM appointments a
        WHERE a.salon_id = %s AND a.worker_id = %s
          AND a.date BETWEEN %s AND %s
          AND a.status IN ('pending', 'scheduled')
        ORDER BY a.date, a.time
        """,
        (salon["id"], worker_id, selected_start, selected_end),
    )
    absence_rows = db_query(
        """
        SELECT start_date, end_date, start_time, end_time, all_day
        FROM worker_time_off
        WHERE salon_id = %s AND worker_id = %s
          AND start_date <= %s AND end_date >= %s
        ORDER BY start_date, start_time NULLS FIRST
        """,
        (salon["id"], worker_id, selected_end, selected_start),
    )

    appointments_by_date = {}
    for row in appointment_rows:
        row_date = parse_iso_date(row["date"])
        row_start = time_to_minutes(row["time"])
        if not row_date or row_start is None:
            continue
        appointments_by_date.setdefault(row_date, []).append(
            (row_start, row_start + int(row.get("duration_minutes") or 30))
        )

    normalized_absences = []
    for row in absence_rows:
        absence_start_date = parse_iso_date(row["start_date"])
        absence_end_date = parse_iso_date(row["end_date"])
        if not absence_start_date or not absence_end_date:
            continue
        normalized_absences.append(
            {
                "start_date": absence_start_date,
                "end_date": absence_end_date,
                "start_time": time_to_minutes(row.get("start_time")),
                "end_time": time_to_minutes(row.get("end_time")),
                "all_day": bool(row["all_day"]),
            }
        )

    first_slot = ((open_minutes + BOOKING_SLOT_MINUTES - 1) // BOOKING_SLOT_MINUTES) * BOOKING_SLOT_MINUTES
    now = local_now()
    available_dates = []

    for offset in range(days):
        current_date = selected_start + timedelta(days=offset)
        current_minutes = now.hour * 60 + now.minute if current_date == now.date() else None
        appointment_ranges = appointments_by_date.get(current_date, [])
        absence_ranges = []
        all_day_absence = False

        for absence in normalized_absences:
            if not (absence["start_date"] <= current_date <= absence["end_date"]):
                continue
            if absence["all_day"] or absence["start_time"] is None or absence["end_time"] is None:
                all_day_absence = True
                break
            absence_ranges.append((absence["start_time"], absence["end_time"]))

        if all_day_absence:
            continue

        slots_count = 0
        for start_minutes in range(first_slot, close_minutes - duration_minutes + 1, BOOKING_SLOT_MINUTES):
            end_minutes = start_minutes + duration_minutes
            if current_minutes is not None and start_minutes <= current_minutes:
                continue
            if any(intervals_overlap(start_minutes, end_minutes, item[0], item[1]) for item in appointment_ranges):
                continue
            if any(intervals_overlap(start_minutes, end_minutes, item[0], item[1]) for item in absence_ranges):
                continue
            slots_count += 1

        if slots_count:
            available_dates.append({"date": current_date.isoformat(), "slots_count": slots_count})

    return {
        "dates": available_dates,
        "price": float(assignment["worker_price"] or 0),
        "duration_minutes": duration_minutes,
        "worker_name": assignment["worker_name"],
        "service_name": assignment["service_name"],
    }, None


def persist_appointment_locked(
    salon,
    client_id,
    service_id,
    worker_id,
    appointment_date,
    appointment_time,
    duration_minutes,
    price,
    status,
    source,
    notes,
    appointment_id=None,
    public_request=False,
):
    connection = get_db()
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM workers WHERE id = %s AND salon_id = %s FOR UPDATE",
                (worker_id, salon["id"]),
            )
            worker = cur.fetchone()
            if not worker:
                connection.rollback()
                return None, "Izabrani radnik ne postoji."

            assignment = worker_service_assignment(
                salon["id"],
                worker_id,
                service_id,
                active_only=status in ("pending", "scheduled"),
                cursor=cur,
            )
            if not assignment:
                connection.rollback()
                return None, "Izabrani radnik ne pruza ovu uslugu."

            if status in ("pending", "scheduled"):
                error = appointment_availability_error(
                    salon,
                    worker_id,
                    appointment_date,
                    appointment_time,
                    duration_minutes,
                    exclude_id=appointment_id,
                    public_request=public_request,
                    cursor=cur,
                    worker=worker,
                )
                if error:
                    connection.rollback()
                    return None, error

            now = datetime.now()
            if appointment_id:
                cur.execute(
                    """
                    UPDATE appointments
                    SET client_id = %s, service_id = %s, worker_id = %s, date = %s, time = %s,
                        duration_minutes = %s, price = %s, status = %s, notes = %s, updated_at = %s
                    WHERE id = %s AND salon_id = %s
                    RETURNING id
                    """,
                    (
                        client_id,
                        service_id,
                        worker_id,
                        appointment_date,
                        appointment_time,
                        duration_minutes,
                        price,
                        status,
                        notes,
                        now,
                        appointment_id,
                        salon["id"],
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO appointments
                        (salon_id, client_id, service_id, worker_id, date, time, duration_minutes,
                         price, status, source, notes, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        salon["id"],
                        client_id,
                        service_id,
                        worker_id,
                        appointment_date,
                        appointment_time,
                        duration_minutes,
                        price,
                        status,
                        source,
                        notes,
                        now,
                        now,
                    ),
                )
            row = cur.fetchone()
        connection.commit()
        if not row:
            return None, "Termin nije pronadjen."
        return row["id"], None
    except Exception:
        connection.rollback()
        raise


@app.route("/")
def home():
    user = current_user()
    if user and user["role"] == "super_admin" and not session.get("impersonate_salon_id"):
        return redirect(url_for("super_admin_dashboard"))
    if user:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db_query("SELECT * FROM users WHERE LOWER(email) = LOWER(%s) AND active = TRUE", (email,), one=True)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_role"] = user["role"]
            session["salon_id"] = user["salon_id"]
            next_url = request.args.get("next") or url_for("home")
            if not next_url.startswith("/"):
                next_url = url_for("home")
            flash("Uspesno ste se prijavili.", "success")
            return redirect(next_url)
        flash("Pogresan email ili sifra.", "error")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    creator = current_user()
    creator_is_super_admin = bool(creator and creator["role"] == "super_admin")
    if request.method == "POST":
        salon_name = request.form.get("salon_name", "").strip()
        owner_name = request.form.get("owner_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        phone = request.form.get("phone", "").strip()
        business_type = request.form.get("business_type", DEFAULT_SALON_SETTINGS["business_type"]).strip()

        if not salon_name or not owner_name or not email or not password:
            flash("Popunite naziv salona, ime vlasnika, email i sifru.", "error")
            return render_template("register.html")
        if len(password) < 8:
            flash("Sifra mora imati najmanje 8 karaktera.", "error")
            return render_template("register.html")
        if db_query("SELECT id FROM users WHERE LOWER(email) = LOWER(%s)", (email,), one=True):
            flash("Vec postoji nalog sa tim emailom.", "error")
            return render_template("register.html")

        slug = unique_slug(salon_name)
        trial_ends_at = local_today() + timedelta(days=TRIAL_DAYS)
        salon_row = db_execute(
            """
            INSERT INTO salons
            (name, slug, business_type, phone, whatsapp, address, working_hours, booking_note,
             open_time, close_time, slot_minutes, owner_name, owner_email,
             subscription_status, subscription_plan, trial_ends_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'trial', 'trial', %s)
            RETURNING id
            """,
            (
                salon_name,
                slug,
                business_type or DEFAULT_SALON_SETTINGS["business_type"],
                phone,
                phone.replace("+", "").replace(" ", ""),
                DEFAULT_SALON_SETTINGS["address"],
                DEFAULT_SALON_SETTINGS["working_hours"],
                DEFAULT_SALON_SETTINGS["booking_note"],
                DEFAULT_SALON_SETTINGS["open_time"],
                DEFAULT_SALON_SETTINGS["close_time"],
                DEFAULT_SALON_SETTINGS["slot_minutes"],
                owner_name,
                email,
                trial_ends_at,
            ),
            returning=True,
        )
        salon_id = salon_row["id"]
        user_row = db_execute(
            """
            INSERT INTO users (salon_id, role, name, email, password_hash, active)
            VALUES (%s, 'owner', %s, %s, %s, TRUE)
            RETURNING id
            """,
            (salon_id, owner_name, email, generate_password_hash(password)),
            returning=True,
        )
        create_default_services(salon_id)
        ensure_salon_default_worker(salon_id)
        if creator_is_super_admin:
            flash("Salon je kreiran i ostajete prijavljeni kao super admin.", "success")
            return redirect(url_for("super_admin_salon_detail", salon_id=salon_id))
        session.clear()
        session["user_id"] = user_row["id"]
        session["user_role"] = "owner"
        session["salon_id"] = salon_id
        flash("Salon je kreiran. Dobijate probni period za testiranje aplikacije.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Odjavljeni ste.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@salon_required
def dashboard():
    salon = current_salon()
    salon_id = salon["id"]
    today = local_today()
    today_str = today.isoformat()
    month_start = today.replace(day=1).isoformat()

    today_count = db_query(
        """
        SELECT COUNT(*) AS total
        FROM appointments
        WHERE salon_id = %s AND date = %s AND status != 'cancelled'
        """,
        (salon_id, today_str),
        one=True,
    )["total"]

    today_revenue = db_query(
        """
        SELECT COALESCE(SUM(price), 0) AS total
        FROM appointments
        WHERE salon_id = %s AND date = %s AND status = 'completed'
        """,
        (salon_id, today_str),
        one=True,
    )["total"]

    month_revenue = db_query(
        """
        SELECT COALESCE(SUM(price), 0) AS total
        FROM appointments
        WHERE salon_id = %s AND date >= %s AND status = 'completed'
        """,
        (salon_id, month_start),
        one=True,
    )["total"]

    pending_count = db_query(
        "SELECT COUNT(*) AS total FROM appointments WHERE salon_id = %s AND status = 'pending'",
        (salon_id,),
        one=True,
    )["total"]

    client_count = db_query("SELECT COUNT(*) AS total FROM clients WHERE salon_id = %s", (salon_id,), one=True)["total"]
    worker_count = db_query(
        "SELECT COUNT(*) AS total FROM workers WHERE salon_id = %s AND active = TRUE",
        (salon_id,),
        one=True,
    )["total"]

    upcoming = db_query(
        """
        SELECT a.*, c.name AS client_name, c.phone AS client_phone, s.name AS service_name,
               w.name AS worker_name
        FROM appointments a
        JOIN clients c ON c.id = a.client_id
        JOIN services s ON s.id = a.service_id
        LEFT JOIN workers w ON w.id = a.worker_id
        WHERE a.salon_id = %s AND a.date >= %s AND a.status IN ('pending', 'scheduled')
        ORDER BY a.date ASC, a.time ASC
        LIMIT 8
        """,
        (salon_id, today_str),
    )

    top_services = db_query(
        """
        SELECT s.name, COUNT(a.id) AS total,
               COALESCE(SUM(CASE WHEN a.status = 'completed' THEN a.price ELSE 0 END), 0) AS revenue
        FROM services s
        LEFT JOIN appointments a ON a.service_id = s.id AND a.salon_id = %s AND a.date >= %s
        WHERE s.salon_id = %s
        GROUP BY s.id
        ORDER BY revenue DESC, total DESC
        LIMIT 5
        """,
        (salon_id, month_start, salon_id),
    )

    revenue_raw = db_query(
        """
        SELECT date, COALESCE(SUM(price), 0) AS total
        FROM appointments
        WHERE salon_id = %s AND date >= %s AND status = 'completed'
        GROUP BY date
        """,
        (salon_id, (today - timedelta(days=6)).isoformat()),
    )
    revenue_map = {row["date"].isoformat() if isinstance(row["date"], date) else str(row["date"]): row["total"] for row in revenue_raw}
    revenue_days = []
    max_revenue = 1
    for i in range(6, -1, -1):
        current = today - timedelta(days=i)
        amount = float(revenue_map.get(current.isoformat(), 0))
        max_revenue = max(max_revenue, amount)
        revenue_days.append({"label": current.strftime("%d.%m"), "amount": amount, "height": 8})
    for item in revenue_days:
        item["height"] = max(8, int((item["amount"] / max_revenue) * 120))

    stats = {
        "today_count": today_count,
        "today_revenue": today_revenue,
        "month_revenue": month_revenue,
        "pending_count": pending_count,
        "client_count": client_count,
        "worker_count": worker_count,
    }
    return render_template(
        "dashboard.html",
        stats=stats,
        upcoming=upcoming,
        top_services=top_services,
        revenue_days=revenue_days,
    )


@app.route("/appointments")
@salon_required
@subscription_required
def appointments():
    salon_id = current_salon()["id"]
    status = request.args.get("status", "").strip()
    date_filter = request.args.get("date", "").strip()
    search = request.args.get("q", "").strip()
    worker_filter = request.args.get("worker_id", "").strip()

    params = [salon_id]
    where = ["a.salon_id = %s"]
    if status:
        where.append("a.status = %s")
        params.append(status)
    if date_filter:
        where.append("a.date = %s")
        params.append(date_filter)
    if worker_filter:
        where.append("a.worker_id = %s")
        params.append(worker_filter)
    if search:
        where.append(
            "(LOWER(c.name) LIKE LOWER(%s) OR c.phone LIKE %s OR LOWER(s.name) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(w.name, '')) LIKE LOWER(%s))"
        )
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern, pattern])

    rows = db_query(
        f"""
        SELECT a.*, c.name AS client_name, c.phone AS client_phone, s.name AS service_name,
               w.name AS worker_name
        FROM appointments a
        JOIN clients c ON c.id = a.client_id
        JOIN services s ON s.id = a.service_id
        LEFT JOIN workers w ON w.id = a.worker_id
        WHERE {' AND '.join(where)}
        ORDER BY a.date ASC, a.time ASC
        """,
        params,
    )
    workers = db_query(
        "SELECT id, name, active FROM workers WHERE salon_id = %s ORDER BY active DESC, name",
        (salon_id,),
    )
    return render_template(
        "appointments.html",
        appointments=rows,
        workers=workers,
        filters={"status": status, "date": date_filter, "q": search, "worker_id": worker_filter},
    )


def appointment_form_context(salon_id):
    services = db_query(
        "SELECT * FROM services WHERE salon_id = %s ORDER BY active DESC, name",
        (salon_id,),
    )
    assignments = db_query(
        """
        SELECT ws.service_id, ws.worker_id, ws.price, ws.duration_minutes, ws.active AS assignment_active,
               w.name AS worker_name, w.active AS worker_active
        FROM worker_services ws
        JOIN workers w ON w.id = ws.worker_id
        JOIN services s ON s.id = ws.service_id
        WHERE w.salon_id = %s AND s.salon_id = %s
        ORDER BY w.active DESC, w.name, s.name
        """,
        (salon_id, salon_id),
    )
    worker_service_data = {}
    for row in assignments:
        worker_service_data.setdefault(str(row["service_id"]), []).append(
            {
                "worker_id": row["worker_id"],
                "worker_name": row["worker_name"],
                "price": float(row["price"] or 0),
                "duration_minutes": int(row["duration_minutes"] or 30),
                "active": bool(row["assignment_active"] and row["worker_active"]),
            }
        )
    return services, worker_service_data


@app.route("/appointments/new", methods=["GET", "POST"])
@salon_required
@subscription_required
def appointment_new():
    salon_id = current_salon()["id"]
    appointment = {}
    if request.method == "POST":
        result = save_appointment()
        if result:
            flash("Termin je dodat.", "success")
            return redirect(url_for("appointments"))
        appointment = dict(request.form)
    services, worker_service_data = appointment_form_context(salon_id)
    return render_template(
        "appointment_form.html",
        appointment=appointment,
        services=services,
        worker_service_data=worker_service_data,
        mode="new",
    )


@app.route("/appointments/<int:appointment_id>/edit", methods=["GET", "POST"])
@salon_required
@subscription_required
def appointment_edit(appointment_id):
    salon_id = current_salon()["id"]
    appointment = db_query(
        """
        SELECT a.*, c.name AS client_name, c.phone AS client_phone, c.email AS client_email,
               s.name AS service_name, w.name AS worker_name
        FROM appointments a
        JOIN clients c ON c.id = a.client_id
        JOIN services s ON s.id = a.service_id
        LEFT JOIN workers w ON w.id = a.worker_id
        WHERE a.id = %s AND a.salon_id = %s
        """,
        (appointment_id, salon_id),
        one=True,
    )
    if not appointment:
        flash("Termin nije pronadjen.", "error")
        return redirect(url_for("appointments"))

    appointment_data = dict(appointment)
    if request.method == "POST":
        result = save_appointment(appointment_id)
        if result:
            flash("Termin je sacuvan.", "success")
            return redirect(url_for("appointments"))
        appointment_data.update(dict(request.form))

    services, worker_service_data = appointment_form_context(salon_id)
    return render_template(
        "appointment_form.html",
        appointment=appointment_data,
        services=services,
        worker_service_data=worker_service_data,
        mode="edit",
    )


def save_appointment(appointment_id=None):
    salon = current_salon()
    salon_id = salon["id"]
    client_name = request.form.get("client_name", "").strip()
    client_phone = request.form.get("client_phone", "").strip()
    service_id = request.form.get("service_id", "").strip()
    worker_id = request.form.get("worker_id", "").strip()
    appointment_date = request.form.get("date", "").strip()
    appointment_time = request.form.get("time", "").strip()
    status = request.form.get("status", "scheduled").strip()
    notes = request.form.get("notes", "").strip()

    if not client_name or not service_id or not worker_id or not appointment_date or not appointment_time:
        flash("Popunite ime klijenta, uslugu, radnika, datum i vreme.", "error")
        return False
    if status not in STATUS_LABELS:
        flash("Status termina nije ispravan.", "error")
        return False

    assignment = worker_service_assignment(
        salon_id,
        worker_id,
        service_id,
        active_only=status in ("pending", "scheduled"),
    )
    if not assignment:
        flash("Izabrani radnik ne pruza ovu uslugu.", "error")
        return False

    duration_minutes = int(assignment["worker_duration_minutes"] or 30)
    price = max(0.0, parse_price(request.form.get("price"), fallback=assignment["worker_price"]))
    client_id = get_or_create_client(salon_id, client_name, client_phone)
    saved_id, error = persist_appointment_locked(
        salon,
        client_id,
        int(service_id),
        int(worker_id),
        appointment_date,
        appointment_time,
        duration_minutes,
        price,
        status,
        "admin",
        notes,
        appointment_id=appointment_id,
    )
    if error:
        flash(error, "error")
        return False
    return bool(saved_id)


@app.route("/appointments/<int:appointment_id>/status", methods=["POST"])
@salon_required
@subscription_required
def appointment_status(appointment_id):
    salon = current_salon()
    salon_id = salon["id"]
    new_status = request.form.get("status", "").strip()
    if new_status not in STATUS_LABELS:
        flash("Nepoznat status.", "error")
        return redirect(request.referrer or url_for("appointments"))
    appointment = db_query(
        "SELECT * FROM appointments WHERE id = %s AND salon_id = %s",
        (appointment_id, salon_id),
        one=True,
    )
    if not appointment:
        flash("Termin nije pronadjen.", "error")
        return redirect(request.referrer or url_for("appointments"))

    if new_status in ("pending", "scheduled"):
        _, error = persist_appointment_locked(
            salon,
            appointment["client_id"],
            appointment["service_id"],
            appointment["worker_id"],
            appointment["date"],
            appointment["time"],
            appointment["duration_minutes"],
            appointment["price"],
            new_status,
            appointment["source"],
            appointment.get("notes") or "",
            appointment_id=appointment_id,
        )
        if error:
            flash(error, "error")
            return redirect(request.referrer or url_for("appointments"))
    else:
        db_execute(
            "UPDATE appointments SET status = %s, updated_at = %s WHERE id = %s AND salon_id = %s",
            (new_status, datetime.now(), appointment_id, salon_id),
        )
    flash("Status termina je promenjen.", "success")
    return redirect(request.referrer or url_for("appointments"))


@app.route("/appointments/<int:appointment_id>/delete", methods=["POST"])
@salon_required
@subscription_required
def appointment_delete(appointment_id):
    salon_id = current_salon()["id"]
    db_execute("DELETE FROM appointments WHERE id = %s AND salon_id = %s", (appointment_id, salon_id))
    flash("Termin je obrisan.", "info")
    return redirect(url_for("appointments"))


@app.route("/clients")
@salon_required
@subscription_required
def clients():
    salon_id = current_salon()["id"]
    search = request.args.get("q", "").strip()
    params = [salon_id]
    where = "WHERE c.salon_id = %s"
    if search:
        where += " AND (LOWER(c.name) LIKE LOWER(%s) OR c.phone LIKE %s)"
        pattern = f"%{search}%"
        params.extend([pattern, pattern])
    rows = db_query(
        f"""
        SELECT c.*,
               COUNT(a.id) AS visits,
               COALESCE(SUM(CASE WHEN a.status = 'completed' THEN a.price ELSE 0 END), 0) AS revenue,
               MAX(a.date) AS last_visit
        FROM clients c
        LEFT JOIN appointments a ON a.client_id = c.id AND a.salon_id = c.salon_id
        {where}
        GROUP BY c.id
        ORDER BY last_visit DESC NULLS LAST, c.created_at DESC
        """,
        params,
    )
    return render_template("clients.html", clients=rows, search=search)


@app.route("/workers")
@salon_required
@subscription_required
def workers():
    salon_id = current_salon()["id"]
    rows = db_query(
        """
        SELECT w.*,
               (SELECT COUNT(*)
                FROM worker_services ws
                JOIN services s ON s.id = ws.service_id
                WHERE ws.worker_id = w.id AND ws.active = TRUE AND s.active = TRUE) AS service_count,
               (SELECT STRING_AGG(s.name, ', ' ORDER BY s.name)
                FROM worker_services ws
                JOIN services s ON s.id = ws.service_id
                WHERE ws.worker_id = w.id AND ws.active = TRUE AND s.active = TRUE) AS service_names,
               (SELECT COUNT(*)
                FROM appointments a
                WHERE a.worker_id = w.id AND a.date >= %s AND a.status IN ('pending', 'scheduled')) AS upcoming_count
        FROM workers w
        WHERE w.salon_id = %s
        ORDER BY w.active DESC, w.name
        """,
        (local_today(), salon_id),
    )
    return render_template("workers.html", workers=rows)


@app.route("/workers/new", methods=["GET", "POST"])
@salon_required
@subscription_required
def worker_new():
    salon_id = current_salon()["id"]
    worker = {}
    if request.method == "POST":
        worker_id = save_worker()
        if worker_id:
            flash("Radnik je dodat.", "success")
            return redirect(url_for("workers"))
        worker = dict(request.form)
    services_rows = db_query("SELECT * FROM services WHERE salon_id = %s ORDER BY active DESC, name", (salon_id,))
    return render_template(
        "worker_form.html",
        worker=worker,
        services=services_rows,
        assignments={},
        mode="new",
    )


@app.route("/workers/<int:worker_id>/edit", methods=["GET", "POST"])
@salon_required
@subscription_required
def worker_edit(worker_id):
    salon_id = current_salon()["id"]
    worker = db_query("SELECT * FROM workers WHERE id = %s AND salon_id = %s", (worker_id, salon_id), one=True)
    if not worker:
        flash("Radnik nije pronadjen.", "error")
        return redirect(url_for("workers"))

    worker_data = dict(worker)
    if request.method == "POST":
        saved_id = save_worker(worker_id)
        if saved_id:
            flash("Podaci radnika su sacuvani.", "success")
            return redirect(url_for("workers"))
        worker_data.update(dict(request.form))

    services_rows = db_query("SELECT * FROM services WHERE salon_id = %s ORDER BY active DESC, name", (salon_id,))
    assignment_rows = db_query("SELECT * FROM worker_services WHERE worker_id = %s", (worker_id,))
    assignments = {row["service_id"]: row for row in assignment_rows}
    return render_template(
        "worker_form.html",
        worker=worker_data,
        services=services_rows,
        assignments=assignments,
        mode="edit",
    )


def save_worker(worker_id=None):
    salon_id = current_salon()["id"]
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    notes = request.form.get("notes", "").strip()
    active = request.form.get("active") == "on"
    if not name:
        flash("Ime radnika je obavezno.", "error")
        return None

    service_rows = db_query("SELECT * FROM services WHERE salon_id = %s ORDER BY id", (salon_id,))
    now = datetime.now()
    connection = get_db()
    try:
        with connection.cursor() as cur:
            if worker_id:
                cur.execute(
                    """
                    UPDATE workers
                    SET name = %s, phone = %s, email = %s, notes = %s, active = %s, updated_at = %s
                    WHERE id = %s AND salon_id = %s
                    RETURNING id
                    """,
                    (name, phone, email, notes, active, now, worker_id, salon_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO workers (salon_id, name, phone, email, notes, active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (salon_id, name, phone, email, notes, active, now, now),
                )
            worker_row = cur.fetchone()
            if not worker_row:
                connection.rollback()
                flash("Radnik nije pronadjen.", "error")
                return None
            worker_id = worker_row["id"]

            for service in service_rows:
                selected = request.form.get(f"service_{service['id']}") == "on"
                price = max(0.0, parse_price(request.form.get(f"price_{service['id']}"), fallback=service["price"]))
                try:
                    duration = int(request.form.get(f"duration_{service['id']}") or service["duration_minutes"])
                except (TypeError, ValueError):
                    duration = int(service["duration_minutes"] or 30)
                duration = max(BOOKING_SLOT_MINUTES, duration)
                if selected:
                    cur.execute(
                        """
                        INSERT INTO worker_services
                            (worker_id, service_id, price, duration_minutes, active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                        ON CONFLICT (worker_id, service_id)
                        DO UPDATE SET price = EXCLUDED.price,
                                      duration_minutes = EXCLUDED.duration_minutes,
                                      active = TRUE,
                                      updated_at = EXCLUDED.updated_at
                        """,
                        (worker_id, service["id"], price, duration, now, now),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE worker_services
                        SET active = FALSE, updated_at = %s
                        WHERE worker_id = %s AND service_id = %s
                        """,
                        (now, worker_id, service["id"]),
                    )
        connection.commit()
        return worker_id
    except Exception:
        connection.rollback()
        raise


@app.route("/workers/<int:worker_id>/toggle", methods=["POST"])
@salon_required
@subscription_required
def worker_toggle(worker_id):
    salon_id = current_salon()["id"]
    worker = db_query("SELECT * FROM workers WHERE id = %s AND salon_id = %s", (worker_id, salon_id), one=True)
    if worker:
        db_execute(
            "UPDATE workers SET active = %s, updated_at = %s WHERE id = %s AND salon_id = %s",
            (not worker["active"], datetime.now(), worker_id, salon_id),
        )
        flash("Status radnika je promenjen.", "success")
    return redirect(url_for("workers"))


@app.route("/workers/<int:worker_id>/delete", methods=["POST"])
@salon_required
@subscription_required
def worker_delete(worker_id):
    salon_id = current_salon()["id"]
    used = db_query(
        "SELECT COUNT(*) AS total FROM appointments WHERE worker_id = %s AND salon_id = %s",
        (worker_id, salon_id),
        one=True,
    )["total"]
    worker_count = db_query(
        "SELECT COUNT(*) AS total FROM workers WHERE salon_id = %s",
        (salon_id,),
        one=True,
    )["total"]
    if used:
        flash("Radnik ima termine i ne moze se obrisati. Mozete ga deaktivirati.", "warning")
    elif worker_count <= 1:
        flash("Salon mora imati bar jednog radnika.", "warning")
    else:
        db_execute("DELETE FROM workers WHERE id = %s AND salon_id = %s", (worker_id, salon_id))
        flash("Radnik je obrisan.", "info")
    return redirect(url_for("workers"))


def parse_calendar_month(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m").date()
        return parsed.replace(day=1)
    except (TypeError, ValueError):
        return local_today().replace(day=1)


@app.route("/calendar", methods=["GET", "POST"])
@salon_required
@subscription_required
def worker_calendar():
    salon_id = current_salon()["id"]
    if request.method == "POST":
        worker_id = request.form.get("worker_id", "").strip()
        start_date = parse_iso_date(request.form.get("start_date", "").strip())
        end_date = parse_iso_date(request.form.get("end_date", "").strip()) or start_date
        all_day = request.form.get("all_day") == "on"
        start_time = request.form.get("start_time", "").strip() or None
        end_time = request.form.get("end_time", "").strip() or None
        reason = request.form.get("reason", "").strip()
        worker = db_query(
            "SELECT * FROM workers WHERE id = %s AND salon_id = %s",
            (worker_id, salon_id),
            one=True,
        )
        if not worker or not start_date or not end_date or end_date < start_date:
            flash("Izaberite radnika i ispravan datum odsustva.", "error")
        elif (end_date - start_date).days > 366:
            flash("Odsustvo ne moze biti duze od godinu dana.", "error")
        elif not all_day and (
            start_date != end_date
            or time_to_minutes(start_time) is None
            or time_to_minutes(end_time) is None
            or time_to_minutes(end_time) <= time_to_minutes(start_time)
        ):
            flash("Delimicno odsustvo mora biti za jedan dan i imati ispravno vreme.", "error")
        else:
            connection = get_db()
            try:
                with connection.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM workers WHERE id = %s AND salon_id = %s FOR UPDATE",
                        (worker_id, salon_id),
                    )
                    if not cur.fetchone():
                        connection.rollback()
                        flash("Radnik nije pronadjen.", "error")
                        return redirect(url_for("worker_calendar"))
                    cur.execute(
                        """
                        INSERT INTO worker_time_off
                            (salon_id, worker_id, start_date, end_date, start_time, end_time, all_day, reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            salon_id,
                            worker_id,
                            start_date,
                            end_date,
                            None if all_day else start_time,
                            None if all_day else end_time,
                            all_day,
                            reason,
                        ),
                    )
                connection.commit()
                flash("Odsustvo je dodato u kalendar.", "success")
            except Exception:
                connection.rollback()
                raise
        month_value = start_date.strftime("%Y-%m") if start_date else local_today().strftime("%Y-%m")
        return redirect(url_for("worker_calendar", month=month_value, worker_id=worker_id))

    month_start = parse_calendar_month(request.args.get("month"))
    if month_start.month == 12:
        next_month_start = date(month_start.year + 1, 1, 1)
    else:
        next_month_start = date(month_start.year, month_start.month + 1, 1)
    month_end = next_month_start - timedelta(days=1)
    previous_month_start = (month_start - timedelta(days=1)).replace(day=1)
    worker_filter = request.args.get("worker_id", "").strip()

    workers_rows = db_query(
        "SELECT * FROM workers WHERE salon_id = %s ORDER BY active DESC, name",
        (salon_id,),
    )
    params = [salon_id, month_end, month_start]
    worker_where = ""
    if worker_filter:
        worker_where = " AND o.worker_id = %s"
        params.append(worker_filter)
    absence_rows = db_query(
        f"""
        SELECT o.*, w.name AS worker_name
        FROM worker_time_off o
        JOIN workers w ON w.id = o.worker_id
        WHERE o.salon_id = %s AND o.start_date <= %s AND o.end_date >= %s {worker_where}
        ORDER BY o.start_date, w.name
        """,
        params,
    )
    absence_by_date = {}
    for absence in absence_rows:
        current = max(absence["start_date"], month_start)
        final = min(absence["end_date"], month_end)
        while current <= final:
            absence_by_date.setdefault(current.isoformat(), []).append(absence)
            current += timedelta(days=1)

    calendar_weeks = []
    for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month):
        calendar_weeks.append(
            [
                {
                    "date": day,
                    "iso": day.isoformat(),
                    "in_month": day.month == month_start.month,
                    "is_today": day == local_today(),
                    "absences": absence_by_date.get(day.isoformat(), []),
                }
                for day in week
            ]
        )

    return render_template(
        "calendar.html",
        workers=workers_rows,
        calendar_weeks=calendar_weeks,
        month_start=month_start,
        previous_month=previous_month_start.strftime("%Y-%m"),
        next_month=next_month_start.strftime("%Y-%m"),
        worker_filter=worker_filter,
        absences=absence_rows,
    )


@app.route("/calendar/time-off/<int:time_off_id>/delete", methods=["POST"])
@salon_required
@subscription_required
def worker_time_off_delete(time_off_id):
    salon_id = current_salon()["id"]
    row = db_query(
        "SELECT * FROM worker_time_off WHERE id = %s AND salon_id = %s",
        (time_off_id, salon_id),
        one=True,
    )
    if row:
        db_execute("DELETE FROM worker_time_off WHERE id = %s AND salon_id = %s", (time_off_id, salon_id))
        flash("Odsustvo je uklonjeno.", "info")
        return redirect(url_for("worker_calendar", month=row["start_date"].strftime("%Y-%m"), worker_id=row["worker_id"]))
    flash("Odsustvo nije pronadjeno.", "error")
    return redirect(url_for("worker_calendar"))


@app.route("/services")
@salon_required
@subscription_required
def services():
    salon_id = current_salon()["id"]
    rows = db_query(
        """
        SELECT s.*,
               COUNT(a.id) AS total_appointments,
               COALESCE(SUM(CASE WHEN a.status = 'completed' THEN a.price ELSE 0 END), 0) AS revenue,
               (SELECT COUNT(*)
                FROM worker_services ws
                JOIN workers w ON w.id = ws.worker_id
                WHERE ws.service_id = s.id AND ws.active = TRUE AND w.active = TRUE) AS worker_count
        FROM services s
        LEFT JOIN appointments a ON a.service_id = s.id AND a.salon_id = s.salon_id
        WHERE s.salon_id = %s
        GROUP BY s.id
        ORDER BY s.active DESC, s.name ASC
        """,
        (salon_id,),
    )
    return render_template("services.html", services=rows)


@app.route("/services/new", methods=["GET", "POST"])
@salon_required
@subscription_required
def service_new():
    if request.method == "POST":
        result = save_service()
        if result:
            flash("Usluga je dodata.", "success")
            return redirect(url_for("services"))
    return render_template("service_form.html", service={}, mode="new")


@app.route("/services/<int:service_id>/edit", methods=["GET", "POST"])
@salon_required
@subscription_required
def service_edit(service_id):
    salon_id = current_salon()["id"]
    service = db_query("SELECT * FROM services WHERE id = %s AND salon_id = %s", (service_id, salon_id), one=True)
    if not service:
        flash("Usluga nije pronadjena.", "error")
        return redirect(url_for("services"))
    if request.method == "POST":
        result = save_service(service_id)
        if result:
            flash("Usluga je sacuvana.", "success")
            return redirect(url_for("services"))
    return render_template("service_form.html", service=dict(service), mode="edit")


def save_service(service_id=None):
    salon_id = current_salon()["id"]
    name = request.form.get("name", "").strip()
    price = max(0.0, parse_price(request.form.get("price"), fallback=0))
    duration = request.form.get("duration_minutes", "30").strip()
    description = request.form.get("description", "").strip()
    active = True if request.form.get("active") == "on" else False

    if not name:
        flash("Naziv usluge je obavezan.", "error")
        return False
    try:
        duration = int(duration)
    except ValueError:
        duration = 30
    duration = max(BOOKING_SLOT_MINUTES, duration)

    try:
        if service_id:
            db_execute(
                """
                UPDATE services
                SET name = %s, price = %s, duration_minutes = %s, description = %s, active = %s
                WHERE id = %s AND salon_id = %s
                """,
                (name, price, duration, description, active, service_id, salon_id),
            )
        else:
            db_execute(
                """
                INSERT INTO services (salon_id, name, price, duration_minutes, description, active)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (salon_id, name, price, duration, description, active),
            )
    except psycopg.errors.UniqueViolation:
        get_db().rollback()
        flash("Vec postoji usluga sa tim nazivom.", "error")
        return False
    return True


@app.route("/services/<int:service_id>/toggle", methods=["POST"])
@salon_required
@subscription_required
def service_toggle(service_id):
    salon_id = current_salon()["id"]
    service = db_query("SELECT * FROM services WHERE id = %s AND salon_id = %s", (service_id, salon_id), one=True)
    if service:
        db_execute("UPDATE services SET active = %s WHERE id = %s AND salon_id = %s", (not service["active"], service_id, salon_id))
        flash("Status usluge je promenjen.", "success")
    return redirect(url_for("services"))


@app.route("/services/<int:service_id>/delete", methods=["POST"])
@salon_required
@subscription_required
def service_delete(service_id):
    salon_id = current_salon()["id"]
    used = db_query("SELECT COUNT(*) AS total FROM appointments WHERE service_id = %s AND salon_id = %s", (service_id, salon_id), one=True)["total"]
    if used:
        flash("Usluga ima termine i ne moze se obrisati. Mozete je deaktivirati.", "warning")
    else:
        db_execute("DELETE FROM services WHERE id = %s AND salon_id = %s", (service_id, salon_id))
        flash("Usluga je obrisana.", "info")
    return redirect(url_for("services"))


@app.route("/settings", methods=["GET", "POST"])
@salon_required
def settings_page():
    salon_id = current_salon()["id"]
    if request.method == "POST":
        booking_mode = request.form.get("booking_mode", "manual").strip()
        if booking_mode not in ("manual", "automatic"):
            booking_mode = "manual"
        value_map = {
            "name": request.form.get("business_name", "").strip(),
            "business_type": request.form.get("business_type", "").strip(),
            "phone": request.form.get("phone", "").strip(),
            "whatsapp": request.form.get("whatsapp", "").strip(),
            "instagram": request.form.get("instagram", "").strip(),
            "address": request.form.get("address", "").strip(),
            "working_hours": request.form.get("working_hours", "").strip(),
            "open_time": request.form.get("open_time", "09:00").strip(),
            "close_time": request.form.get("close_time", "20:00").strip(),
            "slot_minutes": BOOKING_SLOT_MINUTES,
            "booking_mode": booking_mode,
            "booking_note": request.form.get("booking_note", "").strip(),
        }
        open_minutes = time_to_minutes(value_map["open_time"])
        close_minutes = time_to_minutes(value_map["close_time"])
        if not value_map["name"] or open_minutes is None or close_minutes is None or close_minutes <= open_minutes:
            flash("Unesite naziv salona i ispravno vreme otvaranja i zatvaranja.", "error")
            return render_template("settings.html")
        db_execute(
            """
            UPDATE salons
            SET name = %s, business_type = %s, phone = %s, whatsapp = %s, instagram = %s,
                address = %s, working_hours = %s, open_time = %s, close_time = %s,
                slot_minutes = %s, booking_mode = %s, booking_note = %s, updated_at = %s
            WHERE id = %s
            """,
            (
                value_map["name"],
                value_map["business_type"],
                value_map["phone"],
                value_map["whatsapp"],
                value_map["instagram"],
                value_map["address"],
                value_map["working_hours"],
                value_map["open_time"],
                value_map["close_time"],
                value_map["slot_minutes"],
                value_map["booking_mode"],
                value_map["booking_note"],
                datetime.now(),
                salon_id,
            ),
        )
        flash("Podesavanja su sacuvana.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html")


@app.route("/subscription")
@salon_required
def subscription_page():
    return render_template("subscription.html")


@app.route("/zakazi")
def legacy_booking():
    salons = db_query("SELECT slug FROM salons ORDER BY created_at ASC LIMIT 2")
    if len(salons) == 1:
        return redirect(url_for("public_booking", slug=salons[0]["slug"]))
    return render_template("404.html"), 404


def public_booking_context(salon, form_data=None):
    salon_id = salon["id"]
    services_rows = db_query(
        """
        SELECT s.id, s.name, s.description,
               MIN(ws.price) AS min_price,
               MIN(ws.duration_minutes) AS min_duration_minutes
        FROM services s
        JOIN worker_services ws ON ws.service_id = s.id AND ws.active = TRUE
        JOIN workers w ON w.id = ws.worker_id AND w.active = TRUE
        WHERE s.salon_id = %s AND s.active = TRUE
        GROUP BY s.id
        ORDER BY s.name
        """,
        (salon_id,),
    )
    assignment_rows = db_query(
        """
        SELECT ws.service_id, ws.worker_id, ws.price, ws.duration_minutes, w.name AS worker_name
        FROM worker_services ws
        JOIN workers w ON w.id = ws.worker_id
        JOIN services s ON s.id = ws.service_id
        WHERE w.salon_id = %s AND s.salon_id = %s
          AND w.active = TRUE AND s.active = TRUE AND ws.active = TRUE
        ORDER BY w.name
        """,
        (salon_id, salon_id),
    )
    booking_data = {}
    for row in assignment_rows:
        booking_data.setdefault(str(row["service_id"]), []).append(
            {
                "worker_id": row["worker_id"],
                "worker_name": row["worker_name"],
                "price": float(row["price"] or 0),
                "duration_minutes": int(row["duration_minutes"] or 30),
            }
        )
    return {
        "services": services_rows,
        "booking_data": booking_data,
        "settings": salon_settings(salon),
        "salon": salon,
        "form_data": form_data or {},
    }


@app.route("/api/s/<slug>/available-dates")
def public_available_dates(slug):
    salon = db_query("SELECT * FROM salons WHERE slug = %s", (slug,), one=True)
    if not salon or not subscription_is_allowed(salon):
        return jsonify({"ok": False, "error": "Zakazivanje trenutno nije dostupno."}), 404
    try:
        service_id = int(request.args.get("service_id", ""))
        worker_id = int(request.args.get("worker_id", ""))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Izaberite uslugu i radnika."}), 400

    start_date = request.args.get("start_date", "").strip() or None
    result, error = available_dates_for_worker(
        salon,
        worker_id,
        service_id,
        start_date=start_date,
        days=request.args.get("days", "90"),
    )
    if error:
        return jsonify({"ok": False, "error": error, "dates": []}), 400
    response = jsonify({"ok": True, **result})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/s/<slug>/availability")
def public_availability(slug):
    salon = db_query("SELECT * FROM salons WHERE slug = %s", (slug,), one=True)
    if not salon or not subscription_is_allowed(salon):
        return jsonify({"ok": False, "error": "Zakazivanje trenutno nije dostupno."}), 404
    try:
        service_id = int(request.args.get("service_id", ""))
        worker_id = int(request.args.get("worker_id", ""))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Izaberite uslugu i radnika."}), 400
    appointment_date = request.args.get("date", "").strip()
    result, error = available_slots_for_worker(salon, worker_id, service_id, appointment_date)
    if error:
        return jsonify({"ok": False, "error": error, "slots": []}), 400
    response = jsonify({"ok": True, **result})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/s/<slug>/zakazi", methods=["GET", "POST"])
def public_booking(slug):
    salon = db_query("SELECT * FROM salons WHERE slug = %s", (slug,), one=True)
    if not salon:
        return render_template("404.html"), 404
    if not subscription_is_allowed(salon):
        return render_template("booking_unavailable.html", salon=salon, settings=salon_settings(salon)), 403

    salon_id = salon["id"]
    if request.method == "POST":
        client_name = request.form.get("client_name", "").strip()
        client_phone = request.form.get("client_phone", "").strip()
        service_id = request.form.get("service_id", "").strip()
        worker_id = request.form.get("worker_id", "").strip()
        appointment_date = request.form.get("date", "").strip()
        appointment_time = request.form.get("time", "").strip()
        notes = request.form.get("notes", "").strip()
        form_data = dict(request.form)

        try:
            service_id = int(service_id)
            worker_id = int(worker_id)
        except (TypeError, ValueError):
            service_id = None
            worker_id = None

        if not client_name or not client_phone or not service_id or not worker_id or not appointment_date or not appointment_time:
            flash("Popunite ime, telefon, uslugu, radnika, datum i vreme.", "error")
            return render_template("booking.html", **public_booking_context(salon, form_data))

        assignment = worker_service_assignment(salon_id, worker_id, service_id, active_only=True)
        if not assignment:
            flash("Izabrani radnik trenutno ne pruza ovu uslugu.", "error")
            return render_template("booking.html", **public_booking_context(salon, form_data))

        client_id = get_or_create_client(salon_id, client_name, client_phone)
        status = "scheduled" if salon.get("booking_mode") == "automatic" else "pending"
        appointment_id, error = persist_appointment_locked(
            salon,
            client_id,
            service_id,
            worker_id,
            appointment_date,
            appointment_time,
            int(assignment["worker_duration_minutes"] or 30),
            assignment["worker_price"],
            status,
            "public",
            notes,
            public_request=True,
        )
        if error:
            flash(error + " Osvezite listu i izaberite drugi termin.", "error")
            return render_template("booking.html", **public_booking_context(salon, form_data))
        return redirect(url_for("booking_success", slug=slug, appointment_id=appointment_id))

    return render_template("booking.html", **public_booking_context(salon))


@app.route("/s/<slug>/zakazi/uspesno/<int:appointment_id>")
def booking_success(slug, appointment_id):
    salon = db_query("SELECT * FROM salons WHERE slug = %s", (slug,), one=True)
    if not salon:
        return redirect(url_for("legacy_booking"))
    appointment = db_query(
        """
        SELECT a.*, c.name AS client_name, s.name AS service_name, w.name AS worker_name
        FROM appointments a
        JOIN clients c ON c.id = a.client_id
        JOIN services s ON s.id = a.service_id
        LEFT JOIN workers w ON w.id = a.worker_id
        WHERE a.id = %s AND a.salon_id = %s
        """,
        (appointment_id, salon["id"]),
        one=True,
    )
    if not appointment:
        return redirect(url_for("public_booking", slug=slug))
    return render_template("booking_success.html", appointment=appointment, salon=salon)


@app.route("/export/appointments.csv")
@salon_required
@subscription_required
def export_appointments():
    salon_id = current_salon()["id"]
    rows = db_query(
        """
        SELECT a.id, a.date, a.time, a.duration_minutes, c.name AS client, c.phone,
               s.name AS service, w.name AS worker, a.price, a.status, a.source, a.notes
        FROM appointments a
        JOIN clients c ON c.id = a.client_id
        JOIN services s ON s.id = a.service_id
        LEFT JOIN workers w ON w.id = a.worker_id
        WHERE a.salon_id = %s
        ORDER BY a.date DESC, a.time DESC
        """,
        (salon_id,),
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "date", "time", "duration_minutes", "client", "phone", "service", "worker", "price", "status", "source", "notes"])
    for row in rows:
        writer.writerow([row[key] for key in row.keys()])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=appointments.csv"})


@app.route("/export/clients.csv")
@salon_required
@subscription_required
def export_clients():
    salon_id = current_salon()["id"]
    rows = db_query(
        """
        SELECT c.id, c.name, c.phone, c.email, c.notes, c.created_at,
               COUNT(a.id) AS visits,
               COALESCE(SUM(CASE WHEN a.status = 'completed' THEN a.price ELSE 0 END), 0) AS revenue
        FROM clients c
        LEFT JOIN appointments a ON a.client_id = c.id AND a.salon_id = c.salon_id
        WHERE c.salon_id = %s
        GROUP BY c.id
        ORDER BY c.created_at DESC
        """,
        (salon_id,),
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "name", "phone", "email", "notes", "created_at", "visits", "revenue"])
    for row in rows:
        writer.writerow([row[key] for key in row.keys()])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=clients.csv"})


@app.route("/super-admin")
@super_admin_required
def super_admin_dashboard():
    if session.get("impersonate_salon_id"):
        return redirect(url_for("dashboard"))
    stats = db_query(
        """
        SELECT
            COUNT(*) AS total_salons,
            COUNT(*) FILTER (WHERE subscription_status = 'active') AS active_salons,
            COUNT(*) FILTER (WHERE subscription_status = 'trial') AS trial_salons,
            COUNT(*) FILTER (WHERE subscription_status IN ('past_due', 'blocked')) AS problem_salons,
            COUNT(*) FILTER (WHERE subscription_plan = 'monthly' AND subscription_status = 'active') AS monthly_salons,
            COUNT(*) FILTER (WHERE subscription_plan = 'yearly' AND subscription_status = 'active') AS yearly_salons
        FROM salons
        """,
        one=True,
    )
    recent_salons = db_query("SELECT * FROM salons ORDER BY created_at DESC LIMIT 8")
    pending_requests = db_query("SELECT COUNT(*) AS total FROM appointments WHERE status = 'pending'", one=True)["total"]
    calculated = {
        "mrr": int(stats["monthly_salons"] or 0) * MONTHLY_PRICE_EUR,
        "arr": int(stats["yearly_salons"] or 0) * YEARLY_PRICE_EUR,
    }
    return render_template("super_dashboard.html", stats=stats, recent_salons=recent_salons, pending_requests=pending_requests, calculated=calculated)


@app.route("/super-admin/salons")
@super_admin_required
def super_admin_salons():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    params = []
    where = []
    if q:
        where.append("(LOWER(name) LIKE LOWER(%s) OR LOWER(owner_email) LIKE LOWER(%s) OR LOWER(owner_name) LIKE LOWER(%s))")
        pattern = f"%{q}%"
        params.extend([pattern, pattern, pattern])
    if status:
        where.append("subscription_status = %s")
        params.append(status)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    salons = db_query(
        f"""
        SELECT s.*,
               COUNT(DISTINCT c.id) AS client_count,
               COUNT(DISTINCT a.id) AS appointment_count
        FROM salons s
        LEFT JOIN clients c ON c.salon_id = s.id
        LEFT JOIN appointments a ON a.salon_id = s.id
        {where_sql}
        GROUP BY s.id
        ORDER BY s.created_at DESC
        """,
        params,
    )
    return render_template("super_salons.html", salons=salons, filters={"q": q, "status": status})


@app.route("/super-admin/salons/<int:salon_id>")
@super_admin_required
def super_admin_salon_detail(salon_id):
    salon = db_query("SELECT * FROM salons WHERE id = %s", (salon_id,), one=True)
    if not salon:
        flash("Salon nije pronadjen.", "error")
        return redirect(url_for("super_admin_salons"))
    counts = db_query(
        """
        SELECT
            (SELECT COUNT(*) FROM users WHERE salon_id = %s) AS users,
            (SELECT COUNT(*) FROM clients WHERE salon_id = %s) AS clients,
            (SELECT COUNT(*) FROM services WHERE salon_id = %s) AS services,
            (SELECT COUNT(*) FROM appointments WHERE salon_id = %s) AS appointments,
            (SELECT COUNT(*) FROM appointments WHERE salon_id = %s AND status = 'pending') AS pending
        """,
        (salon_id, salon_id, salon_id, salon_id, salon_id),
        one=True,
    )
    users = db_query("SELECT id, name, email, role, active, created_at FROM users WHERE salon_id = %s ORDER BY created_at DESC", (salon_id,))
    recent = db_query(
        """
        SELECT a.*, c.name AS client_name, s.name AS service_name
        FROM appointments a
        JOIN clients c ON c.id = a.client_id
        JOIN services s ON s.id = a.service_id
        WHERE a.salon_id = %s
        ORDER BY a.created_at DESC
        LIMIT 10
        """,
        (salon_id,),
    )
    return render_template("super_salon_detail.html", salon=salon, counts=counts, users=users, recent=recent)


@app.route("/super-admin/salons/<int:salon_id>/subscription", methods=["POST"])
@super_admin_required
def super_admin_update_subscription(salon_id):
    status = request.form.get("subscription_status", "trial").strip()
    plan = request.form.get("subscription_plan", "trial").strip()
    trial_ends_at = request.form.get("trial_ends_at", "").strip() or None
    if status not in SUBSCRIPTION_LABELS:
        status = "trial"
    if plan not in PLAN_LABELS:
        plan = "trial"
    db_execute(
        """
        UPDATE salons
        SET subscription_status = %s, subscription_plan = %s, trial_ends_at = %s, updated_at = %s
        WHERE id = %s
        """,
        (status, plan, trial_ends_at, datetime.now(), salon_id),
    )
    db_execute(
        """
        INSERT INTO subscription_events (salon_id, provider, event_type, payload)
        VALUES (%s, 'manual', 'super_admin_update', jsonb_build_object('status', %s, 'plan', %s))
        """,
        (salon_id, status, plan),
    )
    flash("Pretplata je azurirana.", "success")
    return redirect(url_for("super_admin_salon_detail", salon_id=salon_id))


@app.route("/super-admin/salons/<int:salon_id>/impersonate", methods=["POST"])
@super_admin_required
def super_admin_impersonate(salon_id):
    salon = db_query("SELECT id FROM salons WHERE id = %s", (salon_id,), one=True)
    if not salon:
        flash("Salon nije pronadjen.", "error")
        return redirect(url_for("super_admin_salons"))
    session["impersonate_salon_id"] = salon_id
    flash("Otvoren je panel izabranog salona. Mozete se vratiti u super admin panel iz gornje navigacije.", "info")
    return redirect(url_for("dashboard"))


@app.route("/super-admin/stop-impersonating")
@super_admin_required
def super_admin_stop_impersonating():
    session.pop("impersonate_salon_id", None)
    return redirect(url_for("super_admin_dashboard"))


@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True)
