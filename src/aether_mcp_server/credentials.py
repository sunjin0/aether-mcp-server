"""短期、加密的调用方 SMTP 凭据令牌验证。"""
import base64
import hashlib
import json
import os
from time import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialTokenError(ValueError):
    pass


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
    if not isinstance(credential, dict) or not all(isinstance(credential.get(key), str) for key in ("sender_email", "smtp_authorization_code")):
        raise CredentialTokenError("邮件凭据令牌无效")
    return {"sender_email": credential["sender_email"], "smtp_authorization_code": credential["smtp_authorization_code"], "credential_ref": str(payload.get("credentialRef", ""))}
