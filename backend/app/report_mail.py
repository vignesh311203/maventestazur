"""Send a private, administrator-authorized report over TLS. No credentials in logs."""
import smtplib
import ssl
from email.message import EmailMessage

def email_ready(settings):
    return bool(settings.smtp_host and settings.smtp_from_email and settings.smtp_username and settings.smtp_password.get_secret_value() and settings.smtp_port in {465, 587})

def send_report(settings, recipient, archive):
    if not email_ready(settings):
        raise RuntimeError("SMTP not configured")
    message = EmailMessage()
    message["Subject"] = "MetroEstate private business report"
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content("Attached is the requested private MetroEstate reporting copy. It contains customer information. Restrict access and delete obsolete copies according to your retention policy.")
    message.add_attachment(archive, maintype="application", subtype="zip", filename="metroestate-private-report.zip")
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        client = smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=12, context=context)
    else:
        client = smtplib.SMTP(settings.smtp_host, 587, timeout=12)
    with client:
        if settings.smtp_port == 587:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        refused = client.send_message(message)
        if refused:
            raise RuntimeError("Recipient refused")
