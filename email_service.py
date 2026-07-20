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


def _configuration():
    return {
        "api_key": os.environ.get("BREVO_API_KEY", "").strip(),
        "sender_email": os.environ.get("BREVO_SENDER_EMAIL", "").strip(),
        "sender_name": os.environ.get("BREVO_SENDER_NAME", "SalonPanel").strip() or "SalonPanel",
    }


def email_is_configured():
    config = _configuration()
    return bool(config["api_key"] and config["sender_email"])


def send_email(recipient, subject, html_content, recipient_name=""):
    recipient = (recipient or "").strip().lower()
    if not recipient:
        return False, "Email adresa primaoca nije uneta."

    config = _configuration()
    if not config["api_key"] or not config["sender_email"]:
        return False, "Brevo nije podesen na serveru."

    payload = {
        "sender": {"name": config["sender_name"], "email": config["sender_email"]},
        "to": [{"email": recipient, "name": recipient_name or recipient}],
        "subject": subject,
        "htmlContent": html_content,
    }
    req = urllib_request.Request(
        BREVO_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"accept": "application/json", "api-key": config["api_key"], "content-type": "application/json"},
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


def _layout(title, intro, content, footer="Automatska poruka poslata preko SalonPanel aplikacije."):
    return f"""<!doctype html>
<html><body style="margin:0;background:#f4f6f8;font-family:Arial,sans-serif;color:#17202a;">
<div style="max-width:620px;margin:0 auto;padding:28px 14px;">
  <div style="background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.08);">
    <div style="background:#111827;color:#fff;padding:24px 28px;">
      <div style="font-size:14px;opacity:.8;letter-spacing:.08em;text-transform:uppercase;">SalonPanel</div>
      <h1 style="margin:8px 0 0;font-size:25px;">{title}</h1>
    </div>
    <div style="padding:28px;">
      <p style="font-size:16px;line-height:1.6;margin-top:0;">{intro}</p>
      {content}
    </div>
  </div>
  <p style="text-align:center;color:#6b7280;font-size:12px;margin:18px 0 0;">{footer}</p>
</div></body></html>"""


def _appointment_box(data):
    esc = lambda value: html.escape(str(value or ""))
    address = esc(data.get("salon_address"))
    phone = esc(data.get("salon_phone"))
    rows = [
        ("Usluga", esc(data.get("service_name"))),
        ("Radnik", esc(data.get("worker_name") or "Nije navedeno")),
        ("Datum", _date_sr(data.get("date"))),
        ("Vreme", _time_short(data.get("time"))),
    ]
    if address:
        rows.append(("Adresa", address))
    if phone:
        rows.append(("Telefon", phone))
    rendered = "".join(f'<p style="margin:6px 0;"><strong>{label}:</strong> {value}</p>' for label, value in rows)
    return f'<div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:14px;padding:18px 20px;margin:22px 0;">{rendered}</div>'


def send_appointment_message(data, message_type):
    recipient = (data.get("client_email") or "").strip()
    if not recipient:
        return False, "Klijent nema unetu email adresu."

    esc = lambda value: html.escape(str(value or ""))
    client_name = esc(data.get("client_name"))
    salon_name = esc(data.get("salon_name") or "SalonPanel")
    variants = {
        "request_received": (
            "Zahtev za termin je primljen",
            f"Zdravo {client_name}, salon <strong>{salon_name}</strong> je primio vaš zahtev za termin.",
            "Salon će potvrditi ili odbiti zahtev. Do tada je izabrano vreme privremeno sačuvano za vas.",
            f"Zahtev za termin - {data.get('salon_name') or 'SalonPanel'}",
        ),
        "confirmed": (
            "Termin je potvrđen ✅",
            f"Zdravo {client_name}, vaš termin u salonu <strong>{salon_name}</strong> je uspešno potvrđen.",
            "Vidimo se!",
            f"Termin potvrđen - {data.get('salon_name') or 'SalonPanel'}",
        ),
        "updated": (
            "Termin je izmenjen",
            f"Zdravo {client_name}, detalji vašeg termina u salonu <strong>{salon_name}</strong> su promenjeni.",
            "Molimo proverite novi datum, vreme, uslugu i radnika ispod.",
            f"Izmena termina - {data.get('salon_name') or 'SalonPanel'}",
        ),
        "cancelled": (
            "Termin je otkazan",
            f"Zdravo {client_name}, vaš termin u salonu <strong>{salon_name}</strong> je otkazan.",
            "Za novi termin kontaktirajte salon ili ponovo koristite njihov javni link za zakazivanje.",
            f"Termin otkazan - {data.get('salon_name') or 'SalonPanel'}",
        ),
        "reminder_24h": (
            "Podsetnik za termin sutra",
            f"Zdravo {client_name}, podsećamo vas na termin u salonu <strong>{salon_name}</strong>.",
            "Ako ne možete da dođete, obavestite salon na vreme.",
            f"Podsetnik za termin - {data.get('salon_name') or 'SalonPanel'}",
        ),
        "reminder_2h": (
            "Termin počinje uskoro",
            f"Zdravo {client_name}, vaš termin u salonu <strong>{salon_name}</strong> počinje uskoro.",
            "Vidimo se!",
            f"Termin uskoro - {data.get('salon_name') or 'SalonPanel'}",
        ),
    }
    if message_type not in variants:
        return False, "Nepoznat tip email poruke."
    title, intro, ending, subject = variants[message_type]
    body = _layout(title, intro, _appointment_box(data) + f'<p style="font-size:15px;line-height:1.6;margin-bottom:0;">{ending}</p>')
    return send_email(recipient, subject, body, data.get("client_name") or recipient)


def send_salon_new_request(data):
    recipient = (data.get("owner_email") or "").strip()
    if not recipient:
        return False, "Salon nema unetu email adresu vlasnika."
    esc = lambda value: html.escape(str(value or ""))
    if data.get("status") == "scheduled":
        title = "Novi termin je zakazan"
        intro = f"Klijent <strong>{esc(data.get('client_name'))}</strong> je zakazao novi termin."
        subject = f"Novi termin - {data.get('salon_name') or 'SalonPanel'}"
    else:
        title = "Novi zahtev za termin"
        intro = f"Stigao je novi zahtev za termin od klijenta <strong>{esc(data.get('client_name'))}</strong>."
        subject = f"Novi zahtev - {data.get('salon_name') or 'SalonPanel'}"
    content = _appointment_box(data)
    if data.get("admin_url"):
        content += f'<p style="margin-top:22px;"><a href="{html.escape(data["admin_url"])}" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:12px 18px;border-radius:10px;">Otvori termine</a></p>'
    body = _layout(title, intro, content)
    return send_email(recipient, subject, body, data.get("owner_name") or recipient)


def send_auth_email(recipient, recipient_name, action_url, message_type):
    safe_name = html.escape(recipient_name or "")
    safe_url = html.escape(action_url or "")
    if message_type == "verify":
        title = "Potvrdite email adresu"
        intro = f"Zdravo {safe_name}, potvrdite email adresu kako biste zaštitili SalonPanel nalog."
        button = "Potvrdi email"
        subject = "Potvrdite SalonPanel email adresu"
        note = "Link važi 24 sata."
    elif message_type == "reset":
        title = "Promena lozinke"
        intro = f"Zdravo {safe_name}, primili smo zahtev za promenu lozinke vašeg SalonPanel naloga."
        button = "Postavi novu lozinku"
        subject = "Promena SalonPanel lozinke"
        note = "Link važi 60 minuta. Ako niste poslali zahtev, zanemarite ovu poruku."
    else:
        return False, "Nepoznat tip autentifikacionog emaila."
    content = f"""
      <p style="margin:24px 0;"><a href="{safe_url}" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:13px 20px;border-radius:10px;font-weight:700;">{button}</a></p>
      <p style="font-size:14px;color:#6b7280;line-height:1.6;">{note}</p>
      <p style="font-size:12px;color:#94a3b8;word-break:break-all;">{safe_url}</p>
    """
    return send_email(recipient, subject, _layout(title, intro, content), recipient_name or recipient)
