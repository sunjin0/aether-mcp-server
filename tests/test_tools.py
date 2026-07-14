from datetime import datetime

from aether_mcp_server.tools import current_time, echo


def test_echo_returns_the_supplied_message() -> None:
    assert echo("Hello MCP") == "Hello MCP"


def test_current_time_returns_a_utc_iso_timestamp() -> None:
    value = current_time()
    parsed = datetime.fromisoformat(value)

    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
