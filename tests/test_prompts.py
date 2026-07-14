from aether_mcp_server.prompts import greet


def test_greet_returns_a_personalized_message() -> None:
    assert greet("Ada") == "Greet Ada warmly and offer assistance."
