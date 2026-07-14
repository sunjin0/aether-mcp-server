import argparse

from .server import mcp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Aether MCP Server.")
    subparsers = parser.add_subparsers(dest="transport")
    subparsers.add_parser("stdio")
    http_parser = subparsers.add_parser("http")
    http_parser.add_argument("--host", default="127.0.0.1")
    http_parser.add_argument("--port", default=8000, type=int)
    parser.set_defaults(transport="stdio", host="127.0.0.1", port=8000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
        return
    mcp.run()


if __name__ == "__main__":
    main()
