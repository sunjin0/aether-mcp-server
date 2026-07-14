import pytest

from aether_mcp_server.auth import StaticTokenVerifier, load_tokens
from aether_mcp_server.server import create_server


def test_load_tokens_trims_values_and_ignores_empty_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AETHER_MCP_TOKENS", " token-a, ,token-b ")

    assert load_tokens() == frozenset({"token-a", "token-b"})


def test_load_tokens_rejects_missing_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AETHER_MCP_TOKENS", raising=False)

    with pytest.raises(ValueError, match="AETHER_MCP_TOKENS"):
        load_tokens()


@pytest.mark.anyio
async def test_verifier_returns_access_token_only_for_configured_token() -> None:
    verifier = StaticTokenVerifier(frozenset({"token-a"}))

    access_token = await verifier.verify_token("token-a")

    assert access_token is not None
    assert access_token.token == "token-a"
    assert await verifier.verify_token("wrong-token") is None


def test_http_server_uses_the_supplied_token_verifier() -> None:
    verifier = StaticTokenVerifier(frozenset({"token-a"}))

    server = create_server(verifier)

    assert server._token_verifier is verifier
