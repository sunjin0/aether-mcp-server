from unittest.mock import MagicMock, patch

from aether_mcp_server.tools import send_email


CREDENTIAL = {"sender_email": "caller@example.com", "smtp_authorization_code": "never-log-this"}


def test_sends_email_over_ssl_without_returning_secret() -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    with patch("aether_mcp_server.tools.smtplib.SMTP_SSL", return_value=client) as smtp:
        result = send_email(CREDENTIAL, "mail", "smtp.example.com", 465, "ssl", ["to@example.com"], "Subject", "Body")
    smtp.assert_called_once_with("smtp.example.com", 465, timeout=30)
    client.login.assert_called_once_with("caller@example.com", "never-log-this")
    assert result.smtp_accepted is True
    assert result.delivery_status == "accepted_by_smtp"
    assert "never-log-this" not in result.model_dump_json()


def test_sends_email_over_starttls() -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    with patch("aether_mcp_server.tools.smtplib.SMTP", return_value=client):
        result = send_email(CREDENTIAL, "mail", "smtp.example.com", 587, "starttls", ["to@example.com"], "Subject", "Body")
    client.starttls.assert_called_once()
    assert result.smtp_accepted is True
    assert result.delivery_status == "accepted_by_smtp"
