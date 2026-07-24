import phonenumbers


DEFAULT_PHONE_REGION = "RS"
INVALID_PHONE_MESSAGE = "Telefon nije ispravan."
MISSING_PHONE_MESSAGE = "Telefon nije unet."


def normalize_phone(value, default_region=DEFAULT_PHONE_REGION):
    """Return an E.164 phone number and no error, or a clear validation error."""
    text = str(value or "").strip()
    if not text:
        return None, MISSING_PHONE_MESSAGE

    try:
        parsed = phonenumbers.parse(text, default_region)
    except phonenumbers.NumberParseException:
        return None, INVALID_PHONE_MESSAGE

    if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
        return None, INVALID_PHONE_MESSAGE

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164), None
