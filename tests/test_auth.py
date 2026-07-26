import pytest
from starlette.testclient import TestClient

from aether_mcp_server.auth import StaticTokenVerifier, load_tokens
from aether_mcp_server.server import create_server
from aether_mcp_server.tools import DocumentProcessingResult


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


def test_process_document_rest_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_server()

    def fake_process_document(**_kwargs: object) -> DocumentProcessingResult:
        return DocumentProcessingResult(markdown="# 文档", metadata={"pages": 1})

    monkeypatch.setattr("aether_mcp_server.server.process_document", fake_process_document)

    with TestClient(server.streamable_http_app()) as client:
        response = client.post(
            "/api/process-document",
            json={"source": "https://example.com/document.pdf"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "markdown": "# 文档",
        "json_data": None,
        "metadata": {"pages": 1},
    }


def test_process_document_rest_endpoint_rejects_invalid_payload() -> None:
    server = create_server()

    with TestClient(server.streamable_http_app()) as client:
        response = client.post("/api/process-document", json={})

    assert response.status_code == 422


def test_authenticated_rest_endpoint_requires_a_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = create_server(StaticTokenVerifier(frozenset({"token-a"})))
    monkeypatch.setattr(
        "aether_mcp_server.server.process_document",
        lambda **_kwargs: DocumentProcessingResult(markdown="# 文档"),
    )

    with TestClient(server.streamable_http_app()) as client:
        unauthorized = client.post(
            "/api/process-document",
            json={"source": "https://example.com/document.pdf"},
        )
        authorized = client.post(
            "/api/process-document",
            json={"source": "https://example.com/document.pdf"},
            headers={"Authorization": "Bearer token-a"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
