import hmac
import os

from mcp.server.auth.provider import AccessToken


def load_tokens() -> frozenset[str]:
    tokens = frozenset(
        token.strip()
        for token in os.environ.get("AETHER_MCP_TOKENS", "").split(",")
        if token.strip()
    )
    if not tokens:
        raise ValueError("HTTP mode requires the AETHER_MCP_TOKENS environment variable.")
    return tokens


class StaticTokenVerifier:
    def __init__(self, tokens: frozenset[str]) -> None:
        self._tokens = tokens

    async def verify_token(self, token: str) -> AccessToken | None:
        if not any(hmac.compare_digest(token, configured) for configured in self._tokens):
            return None
        return AccessToken(token=token, client_id="aether-mcp-client", scopes=[])
