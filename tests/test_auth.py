import pytest
import jwt
from datetime import UTC, datetime, timedelta
from starlette.testclient import TestClient

from aether_mcp_server.auth import JavaDelegationVerifier, load_delegation_secret
from aether_mcp_server.server import DelegatedToolScopeMiddleware, create_server
from aether_mcp_server.tools import DocumentProcessingResult


DELEGATION_SECRET = "delegation-secret-for-unit-tests-000"


def test_load_delegation_secret_returns_configured_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AETHER_MCP_DELEGATION_SECRET", " " + DELEGATION_SECRET + " ")

    assert load_delegation_secret() == DELEGATION_SECRET


def test_load_delegation_secret_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AETHER_MCP_DELEGATION_SECRET", raising=False)

    assert load_delegation_secret() is None


@pytest.mark.anyio
async def test_verifier_rejects_non_java_tokens() -> None:
    verifier = JavaDelegationVerifier(DELEGATION_SECRET)

    assert await verifier.verify_token("token-a") is None
    assert await verifier.verify_token("wrong-token") is None


@pytest.mark.anyio
async def test_verifier_accepts_only_valid_delegated_tool_scopes() -> None:
    verifier = JavaDelegationVerifier(DELEGATION_SECRET)
    token = jwt.encode(
        {
            "runId": "run-1", "userId": "user-1", "agentId": "agent-1",
            "allowedTools": ["get_current_time"],
            "exp": datetime.now(UTC) + timedelta(minutes=1),
        },
        DELEGATION_SECRET,
        algorithm="HS256",
    )
    access_token = await verifier.verify_token(token)
    assert access_token is not None
    assert access_token.scopes == ["get_current_time"]


@pytest.mark.anyio
async def test_verifier_rejects_expired_or_malformed_delegation_token() -> None:
    verifier = JavaDelegationVerifier(DELEGATION_SECRET)
    expired = jwt.encode(
        {"runId": "run-1", "userId": "user-1", "agentId": "agent-1", "allowedTools": [], "exp": datetime.now(UTC) - timedelta(minutes=1)},
        DELEGATION_SECRET, algorithm="HS256",
    )
    assert await verifier.verify_token(expired) is None


def test_http_server_does_not_require_a_token_for_tool_discovery() -> None:
    verifier = JavaDelegationVerifier(DELEGATION_SECRET)

    server = create_server(verifier)

    assert server._token_verifier is None


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
    server = create_server(JavaDelegationVerifier(DELEGATION_SECRET))
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
            headers={"Authorization": "Bearer " + jwt.encode(
                {
                    "runId": "run-1", "userId": "user-1", "agentId": "agent-1",
                    "allowedTools": ["process_document"],
                    "exp": datetime.now(UTC) + timedelta(minutes=1),
                },
                DELEGATION_SECRET,
                algorithm="HS256",
            )},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_authenticated_rest_endpoint_rejects_token_without_the_tool_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = create_server(JavaDelegationVerifier(DELEGATION_SECRET))
    monkeypatch.setattr(
        "aether_mcp_server.server.process_document",
        lambda **_kwargs: DocumentProcessingResult(markdown="# 文档"),
    )
    token = jwt.encode(
        {
            "runId": "run-1", "userId": "user-1", "agentId": "agent-1",
            "allowedTools": ["get_current_time"],
            "exp": datetime.now(UTC) + timedelta(minutes=1),
        },
        DELEGATION_SECRET,
        algorithm="HS256",
    )

    with TestClient(server.streamable_http_app()) as client:
        response = client.post(
            "/api/process-document",
            json={"source": "https://example.com/document.pdf"},
            headers={"Authorization": "Bearer " + token},
        )

    assert response.status_code == 401


def test_middleware_passes_multipart_upload_to_authorized_rest_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = create_server(JavaDelegationVerifier(DELEGATION_SECRET))
    monkeypatch.setattr(
        "aether_mcp_server.server.process_document",
        lambda **_kwargs: DocumentProcessingResult(markdown="# 图片文字"),
    )
    token = jwt.encode(
        {
            "runId": "run-1", "userId": "user-1", "agentId": "agent-1",
            "allowedTools": ["process_document"],
            "exp": datetime.now(UTC) + timedelta(minutes=1),
        },
        DELEGATION_SECRET,
        algorithm="HS256",
    )

    app = DelegatedToolScopeMiddleware(
        server.streamable_http_app(), JavaDelegationVerifier(DELEGATION_SECRET)
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/convert-file",
            files={"file": ("image.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
            data={"ocr": "true"},
            headers={"Authorization": "Bearer " + token},
        )

    assert response.status_code == 200
    assert response.json()["markdown"] == "# 图片文字"
