import base64
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aether_mcp_server.credentials import CredentialTokenError, decrypt_connector_credential


def _token(payload: dict) -> str:
    nonce = b"0123456789ab"
    encrypted = AESGCM(hashlib.sha256(b"shared-secret").digest()).encrypt(
        nonce, json.dumps(payload).encode(), None
    )
    encoder = base64.urlsafe_b64encode
    return encoder(nonce).decode().rstrip("=") + "." + encoder(encrypted).decode().rstrip("=")


def _claims():
    return {"runId": "run-1", "userId": "user-1"}


def test_decrypt_connector_credential_accepts_java_compatible_payload(monkeypatch):
    monkeypatch.setenv("AETHER_MCP_CREDENTIAL_SECRET", "shared-secret")
    token = _token({"runId": "run-1", "userId": "user-1", "tenantId": "tenant-1",
                    "connectorId": "prom-1", "allowedTools": ["prometheus_query"],
                    "credential": {"endpoint": "https://prometheus.internal", "token": "secret"},
                    "exp": time.time() + 300})

    result = decrypt_connector_credential(token, _claims(), "prometheus_query")
    assert result["tenantId"] == "tenant-1"
    assert result["credential"]["token"] == "secret"


@pytest.mark.parametrize("claims,tool", [
    ({"runId": "other", "userId": "user-1"}, "prometheus_query"),
    ({"runId": "run-1", "userId": "user-1"}, "grafana_query"),
])
def test_decrypt_connector_credential_rejects_scope_mismatch(monkeypatch, claims, tool):
    monkeypatch.setenv("AETHER_MCP_CREDENTIAL_SECRET", "shared-secret")
    token = _token({"runId": "run-1", "userId": "user-1", "tenantId": "tenant-1",
                    "connectorId": "prom-1", "allowedTools": ["prometheus_query"],
                    "credential": {"token": "secret"}, "exp": time.time() + 300})
    with pytest.raises(CredentialTokenError):
        decrypt_connector_credential(token, claims, tool)


def test_decrypt_connector_credential_rejects_expired_token(monkeypatch):
    monkeypatch.setenv("AETHER_MCP_CREDENTIAL_SECRET", "shared-secret")
    token = _token({"runId": "run-1", "userId": "user-1", "tenantId": "tenant-1",
                    "connectorId": "prom-1", "allowedTools": ["prometheus_query"],
                    "credential": {"token": "secret"}, "exp": time.time() - 1})
    with pytest.raises(CredentialTokenError, match="过期"):
        decrypt_connector_credential(token, _claims(), "prometheus_query")
