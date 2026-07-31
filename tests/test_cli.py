import sys

import pytest

import aether_mcp_server.__main__ as cli


def test_cli_defaults_to_stdio() -> None:
    args = cli.build_parser().parse_args([])

    assert args.command == "stdio"


def test_http_cli_accepts_host_and_port() -> None:
    args = cli.build_parser().parse_args(["http", "--host", "0.0.0.0", "--port", "9000"])

    assert args.command == "http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_http_cli_defaults_to_disabled_authentication() -> None:
    args = cli.build_parser().parse_args(["http"])

    assert args.auth is False


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

    async def run_http_server(*args, **kwargs) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(sys, "argv", ["aether-mcp-server", "http", "--host", "0.0.0.0", "--port", "9000"])
    monkeypatch.setattr(cli, "create_server", lambda verifier, host, port: cli.mcp)
    monkeypatch.setattr(cli, "run_http_server", run_http_server)
    monkeypatch.setattr(cli.mcp.settings, "host", cli.mcp.settings.host)
    monkeypatch.setattr(cli.mcp.settings, "port", cli.mcp.settings.port)

    cli.main()

    assert cli.mcp.settings.host == "0.0.0.0"
    assert cli.mcp.settings.port == 9000
    assert calls == [((cli.mcp, None, "0.0.0.0", 9000), {})]


def test_http_mode_rejects_missing_delegation_secret_when_authentication_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AETHER_MCP_DELEGATION_SECRET", raising=False)
    monkeypatch.setattr(sys, "argv", ["aether-mcp-server", "http", "--auth"])

    with pytest.raises(ValueError, match="AETHER_MCP_DELEGATION_SECRET"):
        cli.main()
