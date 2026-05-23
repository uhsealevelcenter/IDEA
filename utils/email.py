import os
import smtplib
from email.message import EmailMessage


class EmailConfigurationError(RuntimeError):
    """Raised when SMTP settings are incomplete."""


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def send_password_reset_email(*, to_email: str, reset_url: str, expires_minutes: int) -> None:
    """Send a password reset link through the configured SMTP server."""
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", "IDEA <idea-dev-grp@hawaii.edu>").strip()
    use_tls = _get_bool_env("SMTP_USE_TLS", True)

    if not smtp_host or not smtp_from:
        raise EmailConfigurationError("SMTP_HOST and SMTP_FROM are required to send password reset emails")

    message = EmailMessage()
    message["Subject"] = "Reset your IDEA password"
    message["From"] = smtp_from
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                "We received a request to reset your IDEA password.",
                "",
                f"Use this link within {expires_minutes} minutes to choose a new password:",
                reset_url,
                "",
                "If you did not request this, you can ignore this email.",
            ]
        )
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        if use_tls:
            server.starttls()
        if smtp_user or smtp_password:
            server.login(smtp_user, smtp_password)
        server.send_message(message)
