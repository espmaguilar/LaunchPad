"""Supervisor notification channel (one place to swap email -> SMS).

NOTIFY_CHANNEL=email (default): Amazon SNS topic email (store.send_alert).
NOTIFY_CHANNEL=sms: placeholder for Twilio. Not wired yet - falls back to email
  so a misconfigured demo still alerts someone. To add Twilio later, implement
  _send_sms() with the Twilio client (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN /
  TWILIO_FROM in .env, supervisor phone per zone) and keep the email fallback.
"""
import config
import store


def channel_label():
    return "SMS" if config.NOTIFY_CHANNEL == "sms" and _sms_ready() else "email"


def _sms_ready():
    return False  # TODO: Twilio


def send(subject, message, sms_text):
    """subject/message: full email. sms_text: short (<=160 chars) version for SMS."""
    if config.NOTIFY_CHANNEL == "sms" and _sms_ready():
        _send_sms(sms_text)
        return "SMS"
    store.send_alert(subject, message)
    return "email"


def _send_sms(text):
    raise NotImplementedError("Twilio SMS is not wired yet")
