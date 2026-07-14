import argparse

from .auth import StaticTokenVerifier, load_tokens
from .server import create_server, mcp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Aether MCP Server.")
    subparsers = parser.add_subparsers(dest="transport")
    subparsers.add_parser("stdio")
    http_parser = subparsers.add_parser("http")
    http_parser.add_argument(
        "--auth",
        action="store_true",
        help="Enable Bearer token authentication for HTTP requests.",
    )
    http_parser.add_argument("--host", default="127.0.0.1")
    http_parser.add_argument("--port", default=8000, type=int)
    parser.set_defaults(transport="stdio", auth=False, host="127.0.0.1", port=8000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.transport == "http":
        verifier = None
        if args.auth:
            verifier = StaticTokenVerifier(load_tokens())
        http_mcp = create_server(
            verifier,
            host=args.host,
            port=args.port,
        )
        http_mcp.settings.host = args.host
        http_mcp.settings.port = args.port
        http_mcp.run(transport="streamable-http")
        return
    mcp.run()


if __name__ == "__main__":
    main()
