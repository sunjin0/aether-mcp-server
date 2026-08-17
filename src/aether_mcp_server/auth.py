import os

import jwt

from mcp.server.auth.provider import AccessToken


class JavaDelegationVerifier:
    """仅校验由 Java 签发的短期运行委派 JWT。"""

    def __init__(self, delegation_secret: str) -> None:
        self._delegation_secret = delegation_secret

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._delegation_secret:
            return None
        try:
            claims = jwt.decode(token, self._delegation_secret, algorithms=["HS256"], options={"require": ["exp", "runId", "userId", "agentId"]})
            allowed_tools = claims.get("allowedTools", [])
            # scope 直接来自已验签声明，拒绝非字符串列表以避免权限解释不一致。
            if not isinstance(allowed_tools, list) or not all(isinstance(name, str) for name in allowed_tools):
                return None
            return AccessToken(token=token, client_id="aether-java-delegation", scopes=allowed_tools)
        except jwt.PyJWTError:
            return None


def load_delegation_secret() -> str | None:
    secret = os.environ.get("AETHER_MCP_DELEGATION_SECRET", "").strip()
    return secret or None
