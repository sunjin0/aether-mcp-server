import argparse
import os
import sys

# Windows 无 symlink 支持时静默
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from .auth import JavaDelegationVerifier, load_delegation_secret
from .server import create_server, mcp, run_http_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Aether MCP Server.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("stdio")

    http_parser = subparsers.add_parser("http")
    http_parser.add_argument(
        "--auth",
        action="store_true",
        help="Enable Bearer token authentication for HTTP requests.",
    )
    http_parser.add_argument("--host", default="127.0.0.1")
    http_parser.add_argument("--port", default=8000, type=int)

    parser.set_defaults(command="stdio", auth=False, host="127.0.0.1", port=8000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "http":
        verifier = None
        if args.auth:
            delegation_secret = load_delegation_secret()
            if delegation_secret is None:
                raise ValueError("HTTP mode requires AETHER_MCP_DELEGATION_SECRET.")
            verifier = JavaDelegationVerifier(delegation_secret)
        http_mcp = create_server(
            verifier,
            host=args.host,
            port=args.port,
        )
        http_mcp.settings.host = args.host
        http_mcp.settings.port = args.port
        import anyio
        anyio.run(run_http_server, http_mcp, verifier, args.host, args.port)
        return
    mcp.run()


if __name__ == "__main__":
    main()
