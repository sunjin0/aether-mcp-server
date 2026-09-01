"""短期、加密的调用方凭据令牌验证。"""
import base64
import hashlib
import json
import os
from time import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialTokenError(ValueError):
    pass


def decrypt_connector_credential(token: str, delegation_claims: dict[str, object], tool_name: str) -> dict[str, object]:
    """解密并校验连接器凭据；返回值只应存活于当前请求作用域。"""
    secret = os.getenv("AETHER_MCP_CREDENTIAL_SECRET", "").strip()
    if not secret:
        raise CredentialTokenError("连接器凭据服务未配置")
    try:
        nonce_text, encrypted_text = token.split(".", 1)
        nonce = base64.urlsafe_b64decode(nonce_text + "==")
        encrypted = base64.urlsafe_b64decode(encrypted_text + "==")
        payload = json.loads(AESGCM(hashlib.sha256(secret.encode()).digest()).decrypt(
            nonce, encrypted, None
        ))
    except Exception as error:
        raise CredentialTokenError("连接器凭据令牌无效") from error
    if not isinstance(payload, dict) or payload.get("runId") != delegation_claims.get("runId") \
            or payload.get("userId") != delegation_claims.get("userId"):
        raise CredentialTokenError("连接器凭据令牌与当前运行不匹配")
    if not isinstance(payload.get("exp"), (int, float)) or payload["exp"] < time():
        raise CredentialTokenError("连接器凭据令牌已过期")
    tools = payload.get("allowedTools")
    credential = payload.get("credential")
    if not isinstance(payload.get("tenantId"), str) or not payload["tenantId"] \
            or not isinstance(payload.get("connectorId"), str) or not payload["connectorId"] \
            or not isinstance(tools, list) or tool_name not in tools \
            or not isinstance(credential, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in credential.items()):
        raise CredentialTokenError("连接器凭据令牌范围无效")
    return {"tenantId": payload["tenantId"], "connectorId": payload["connectorId"], "credential": credential}


def decrypt_email_credential(token: str, delegation_claims: dict[str, object]) -> dict[str, str]:
    secret = os.getenv("AETHER_MCP_CREDENTIAL_SECRET", "").strip()
    if not secret:
        raise CredentialTokenError("邮件凭据服务未配置")
    try:
        nonce_text, encrypted_text = token.split(".", 1)
        nonce = base64.urlsafe_b64decode(nonce_text + "==")
        encrypted = base64.urlsafe_b64decode(encrypted_text + "==")
        raw = AESGCM(hashlib.sha256(secret.encode()).digest()).decrypt(nonce, encrypted, None)
        payload = json.loads(raw)
    except Exception as error:
        raise CredentialTokenError("邮件凭据令牌无效") from error
    if not isinstance(payload, dict) or payload.get("runId") != delegation_claims.get("runId") or payload.get("userId") != delegation_claims.get("userId"):
        raise CredentialTokenError("邮件凭据令牌与当前运行不匹配")
    if not isinstance(payload.get("exp"), (int, float)) or payload["exp"] < time():
        raise CredentialTokenError("邮件凭据令牌已过期")
    credential = payload.get("credential")
    if not isinstance(credential, dict) or not all(isinstance(credential.get(key), str) for key in ("sender_email", "smtp_authorization_code", "smtp_host", "smtp_port", "security")):
        raise CredentialTokenError("邮件凭据令牌无效")
    if credential["security"] not in ("ssl", "starttls"):
        raise CredentialTokenError("邮件凭据令牌无效")
    try:
        smtp_port = int(credential["smtp_port"])
    except (TypeError, ValueError) as error:
        raise CredentialTokenError("邮件凭据令牌无效") from error
    if not 1 <= smtp_port <= 65535:
        raise CredentialTokenError("邮件凭据令牌无效")
    return {
        "sender_email": credential["sender_email"],
        "smtp_authorization_code": credential["smtp_authorization_code"],
        "smtp_host": credential["smtp_host"],
        "smtp_port": str(smtp_port),
        "security": credential["security"],
    }
