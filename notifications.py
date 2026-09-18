"""
Email notifications for new orders and new chat messages (both order-chat
and direct printer/idea-owner conversations).

Fully controlled by SMTP env vars — if they're not set, sends are just
skipped (logged, not raised), so the app keeps working without email set up:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM

Works with any standard SMTP provider: a Gmail account with an app password,
or free tiers from Brevo, Resend, Mailgun, SendGrid, etc.
"""
import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)


def email_enabled():
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def send_email(app, to_email, subject, body):
    if not email_enabled() or not to_email:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        app.logger.warning("email send failed to %s: %s", to_email, e)
