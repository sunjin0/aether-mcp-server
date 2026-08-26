from unittest.mock import MagicMock, patch

from aether_mcp_server.tools import send_email


CREDENTIAL = {"sender_email": "caller@example.com", "smtp_authorization_code": "never-log-this", "smtp_host": "smtp.example.com", "smtp_port": "465", "security": "ssl"}


def test_sends_email_over_ssl_without_returning_secret() -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    with patch("aether_mcp_server.tools.smtplib.SMTP_SSL", return_value=client) as smtp:
        result = send_email(CREDENTIAL, ["to@example.com"], "Subject", "Body")
    smtp.assert_called_once_with("smtp.example.com", 465, timeout=30)
    client.login.assert_called_once_with("caller@example.com", "never-log-this")
    assert result.smtp_accepted is True
    assert result.delivery_status == "accepted_by_smtp"
    assert "never-log-this" not in result.model_dump_json()


def test_sends_email_over_starttls() -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    credential = {**CREDENTIAL, "smtp_port": "587", "security": "starttls"}
    with patch("aether_mcp_server.tools.smtplib.SMTP", return_value=client):
        result = send_email(credential, ["to@example.com"], "Subject", "Body")
    client.starttls.assert_called_once()
    assert result.smtp_accepted is True
    assert result.delivery_status == "accepted_by_smtp"


def test_allows_common_line_breaks_in_text_and_html_body() -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    text_body = "第一行\n第二行\r\n第三行\r第四行\t缩进"
    html_body = "<p>第一行</p>\n<p>第二行</p>\r\n<p>第三行</p>"
    with patch("aether_mcp_server.tools.smtplib.SMTP_SSL", return_value=client):
        result = send_email(CREDENTIAL, ["to@example.com"], "Subject", text_body, html_body=html_body)
    message = client.send_message.call_args.args[0]
    normalized_text = text_body.replace("\r\n", "\n").replace("\r", "\n")
    normalized_html = html_body.replace("\r\n", "\n").replace("\r", "\n")
    assert normalized_text in message.get_body(preferencelist=("plain",)).get_content()
    assert normalized_html in message.get_body(preferencelist=("html",)).get_content()
    assert result.smtp_accepted is True
