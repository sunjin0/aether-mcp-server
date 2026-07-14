from datetime import UTC, datetime


def echo(message: str) -> str:
    return message


def current_time() -> str:
    return datetime.now(UTC).isoformat()
