import sys

import aether_mcp_server.__main__ as cli


def test_cli_defaults_to_stdio() -> None:
    args = cli.build_parser().parse_args([])

    assert args.transport == "stdio"


def test_http_cli_accepts_host_and_port() -> None:
    args = cli.build_parser().parse_args(["http", "--host", "0.0.0.0", "--port", "9000"])

    assert args.transport == "http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_main_defaults_to_stdio_dispatch(monkeypatch) -> None:
    calls = []

    def run(*args, **kwargs) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(sys, "argv", ["aether-mcp-server"])
    monkeypatch.setattr(cli.mcp, "run", run)

    cli.main()

    assert calls == [((), {})]


def test_main_http_configures_server_then_dispatches(monkeypatch) -> None:
    calls = []

    def run(*args, **kwargs) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(sys, "argv", ["aether-mcp-server", "http", "--host", "0.0.0.0", "--port", "9000"])
    monkeypatch.setattr(cli.mcp, "run", run)
    monkeypatch.setattr(cli.mcp.settings, "host", cli.mcp.settings.host)
    monkeypatch.setattr(cli.mcp.settings, "port", cli.mcp.settings.port)

    cli.main()

    assert cli.mcp.settings.host == "0.0.0.0"
    assert cli.mcp.settings.port == 9000
    assert calls == [((), {"transport": "streamable-http"})]
