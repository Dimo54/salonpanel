import html
import json
import logging
import os
from datetime import date, datetime, time
from urllib import error as urllib_error
from urllib import request as urllib_request

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
LOGGER = logging.getLogger(__name__)


def _date_sr(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y.")
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d.%m.%Y.")
    except (TypeError, ValueError):
        return str(value or "")


def _time_short(value):
    if isinstance(value, time):
        return value.strftime("%H:%M")
    text = str(value or "")
    return text[:5] if len(text) >= 5 else text


def send_appointment_confirmation(data):
    recipient = (data.get("client_email") or "").strip()
    if not recipient:
        return False, "Klijent nema unetu email adresu."

    api_key = os.environ.get("BREVO_API_KEY", "").strip()
    sender_email = os.environ.get("BREVO_SENDER_EMAIL", "").strip()
    sender_name = os.environ.get("BREVO_SENDER_NAME", "SalonPanel").strip() or "SalonPanel"
    if not api_key or not sender_email:
        return False, "Brevo nije podesen na serveru."

    esc = lambda value: html.escape(str(value or ""))
    client_name = esc(data.get("client_name"))
    salon_name = esc(data.get("salon_name") or "SalonPanel")
    service_name = esc(data.get("service_name"))
    worker_name = esc(data.get("worker_name") or "Nije navedeno")
    address = esc(data.get("salon_address"))
    phone = esc(data.get("salon_phone"))
    contact_rows = ""
    if address:
        contact_rows += f'<p style="margin:6px 0;"><strong>Adresa:</strong> {address}</p>'
    if phone:
        contact_rows += f'<p style="margin:6px 0;"><strong>Telefon:</strong> {phone}</p>'

    body = f"""<!doctype html>
<html><body style="margin:0;background:#f4f6f8;font-family:Arial,sans-serif;color:#17202a;">
<div style="max-width:620px;margin:0 auto;padding:28px 14px;">
  <div style="background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.08);">
    <div style="background:#111827;color:#fff;padding:24px 28px;">
      <div style="font-size:14px;opacity:.8;letter-spacing:.08em;text-transform:uppercase;">SalonPanel</div>
      <h1 style="margin:8px 0 0;font-size:25px;">Termin je potvrđen ✅</h1>
    </div>
    <div style="padding:28px;">
      <p style="font-size:16px;line-height:1.6;margin-top:0;">Zdravo {client_name},</p>
      <p style="font-size:16px;line-height:1.6;">Vaš termin u salonu <strong>{salon_name}</strong> je uspešno potvrđen.</p>
      <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:14px;padding:18px 20px;margin:22px 0;">
        <p style="margin:6px 0;"><strong>Usluga:</strong> {service_name}</p>
        <p style="margin:6px 0;"><strong>Radnik:</strong> {worker_name}</p>
        <p style="margin:6px 0;"><strong>Datum:</strong> {_date_sr(data.get('date'))}</p>
        <p style="margin:6px 0;"><strong>Vreme:</strong> {_time_short(data.get('time'))}</p>
        {contact_rows}
      </div>
      <p style="font-size:15px;line-height:1.6;margin-bottom:0;">Vidimo se!</p>
    </div>
  </div>
  <p style="text-align:center;color:#6b7280;font-size:12px;margin:18px 0 0;">Automatska poruka poslata preko SalonPanel aplikacije.</p>
</div></body></html>"""

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": recipient, "name": data.get("client_name") or recipient}],
        "subject": f"Termin potvrđen - {data.get('salon_name') or 'SalonPanel'}",
        "htmlContent": body,
    }
    req = urllib_request.Request(
        BREVO_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"accept": "application/json", "api-key": api_key, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=15) as response:
            return (True, None) if 200 <= response.status < 300 else (False, f"Brevo status {response.status}.")
    except urllib_error.HTTPError as exc:
        LOGGER.error("Brevo HTTP error %s: %s", exc.code, exc.read().decode("utf-8", errors="replace")[:500])
        return False, f"Brevo greska ({exc.code})."
    except Exception:
        LOGGER.exception("Brevo email sending failed")
        return False, "Slanje emaila trenutno nije uspelo."
