# Aether MCP Server Initialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python 3.11 FastMCP demonstration server that exposes example tools, a resource, and a prompt over stdio and Streamable HTTP.

**Architecture:** Keep MCP primitives in separate focused modules. `server.py` creates the shared FastMCP instance and imports the registration modules; `__main__.py` selects stdio or Streamable HTTP from the CLI. Tests call registered primitive functions directly where practical.

**Tech Stack:** Python 3.11, uv, official `mcp` package with FastMCP, pytest.

---

## File Structure

- Create: `pyproject.toml` - project metadata, runtime and test dependencies, pytest configuration.
- Create: `.python-version` - pins Python 3.11 for uv.
- Create: `.gitignore` - excludes virtual environments, Python caches, coverage output, and IDE files.
- Create: `src/aether_mcp_server/__init__.py` - package marker and version.
- Create: `src/aether_mcp_server/tools.py` - registers `echo` and `current_time` on the server.
- Create: `src/aether_mcp_server/resources.py` - registers `example://welcome`.
- Create: `src/aether_mcp_server/prompts.py` - registers the `greet` prompt.
- Create: `src/aether_mcp_server/server.py` - shared FastMCP instance and module registration imports.
- Create: `src/aether_mcp_server/__main__.py` - argparse CLI for `stdio` and `http` transports.
- Create: `tests/test_tools.py` - direct behavior tests for tool functions.
- Create: `tests/test_resources.py` - resource content test.
- Create: `tests/test_prompts.py` - prompt content test.
- Create: `tests/test_cli.py` - CLI parsing tests.
- Create: `README.md` - installation, launch, and client configuration instructions.
- Delete: `main.py` - obsolete FastAPI demonstration.
- Delete: `test_main.http` - obsolete REST request example.

### Task 1: Create Project Metadata and Package Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `src/aether_mcp_server/__init__.py`

- [ ] **Step 1: Add the uv project definition**

```toml
[project]
name = "aether-mcp-server"
version = "0.1.0"
description = "A demonstration MCP server with stdio and Streamable HTTP transports."
readme = "README.md"
requires-python = ">=3.11"
dependencies = ["mcp>=1.0.0"]

[dependency-groups]
dev = ["pytest>=8.0.0"]

[project.scripts]
aether-mcp-server = "aether_mcp_server.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Add version and local-development exclusions**

```text
# .python-version
3.11
```

```gitignore
.venv/
__pycache__/
.pytest_cache/
.coverage
htmlcov/
.idea/
```

```python
# src/aether_mcp_server/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 3: Synchronize dependencies**

Run: `uv sync`

Expected: A `.venv` and `uv.lock` are created, with `mcp` and `pytest` installed.

### Task 2: Implement and Test Example Tools

**Files:**
- Create: `src/aether_mcp_server/tools.py`
- Create: `tests/test_tools.py`

- [ ] **Step 1: Write failing tool tests**

```python
from datetime import datetime

from aether_mcp_server.tools import current_time, echo


def test_echo_returns_the_supplied_message() -> None:
    assert echo("Hello MCP") == "Hello MCP"


def test_current_time_returns_a_utc_iso_timestamp() -> None:
    value = current_time()
    parsed = datetime.fromisoformat(value)

    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'aether_mcp_server.tools'`.

- [ ] **Step 3: Implement pure tool functions**

```python
from datetime import UTC, datetime


def echo(message: str) -> str:
    return message


def current_time() -> str:
    return datetime.now(UTC).isoformat()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`

Expected: PASS with 2 tests passed.

### Task 3: Implement and Test the Example Resource and Prompt

**Files:**
- Create: `src/aether_mcp_server/resources.py`
- Create: `src/aether_mcp_server/prompts.py`
- Create: `tests/test_resources.py`
- Create: `tests/test_prompts.py`

- [ ] **Step 1: Write failing resource and prompt tests**

```python
# tests/test_resources.py
from aether_mcp_server.resources import welcome


def test_welcome_resource_describes_the_server() -> None:
    assert welcome() == "Welcome to Aether MCP Server."
```

```python
# tests/test_prompts.py
from aether_mcp_server.prompts import greet


def test_greet_returns_a_personalized_message() -> None:
    assert greet("Ada") == "Greet Ada warmly and offer assistance."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_resources.py tests/test_prompts.py -v`

Expected: FAIL with missing `resources` and `prompts` modules.

- [ ] **Step 3: Implement the resource and prompt functions**

```python
# src/aether_mcp_server/resources.py
def welcome() -> str:
    return "Welcome to Aether MCP Server."
```

```python
# src/aether_mcp_server/prompts.py
def greet(name: str) -> str:
    return f"Greet {name} warmly and offer assistance."
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_resources.py tests/test_prompts.py -v`

Expected: PASS with 2 tests passed.

### Task 4: Assemble FastMCP Registration and CLI

**Files:**
- Create: `src/aether_mcp_server/server.py`
- Create: `src/aether_mcp_server/__main__.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI parsing tests**

```python
from aether_mcp_server.__main__ import build_parser


def test_cli_defaults_to_stdio() -> None:
    args = build_parser().parse_args([])

    assert args.transport == "stdio"


def test_http_cli_accepts_host_and_port() -> None:
    args = build_parser().parse_args(["http", "--host", "0.0.0.0", "--port", "9000"])

    assert args.transport == "http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'aether_mcp_server.__main__'`.

- [ ] **Step 3: Create the shared server and CLI**

```python
# src/aether_mcp_server/server.py
from mcp.server.fastmcp import FastMCP

from .prompts import greet
from .resources import welcome
from .tools import current_time, echo

mcp = FastMCP("Aether MCP Server")
mcp.tool()(echo)
mcp.tool()(current_time)
mcp.resource("example://welcome")(welcome)
mcp.prompt()(greet)
```

```python
# src/aether_mcp_server/__main__.py
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
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
        return
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run CLI and full unit tests**

Run: `uv run pytest -v`

Expected: PASS with 5 tests passed.

Run: `uv run aether-mcp-server --help`

Expected: Exit code 0 and help text containing `stdio` and `http`.

### Task 5: Add User Documentation and Remove Legacy Demo

**Files:**
- Create: `README.md`
- Delete: `main.py`
- Delete: `test_main.http`

- [ ] **Step 1: Write README usage documentation**

```markdown
# Aether MCP Server

A Python MCP demonstration server exposing tools, a resource, and a prompt over stdio or Streamable HTTP.

## Setup

```powershell
uv sync
```

## Run

Run over stdio, the default transport for local MCP clients:

```powershell
uv run aether-mcp-server
```

Run over Streamable HTTP:

```powershell
uv run aether-mcp-server http --host 127.0.0.1 --port 8000
```

The HTTP MCP endpoint is available at `http://127.0.0.1:8000/mcp`.

## Available MCP Primitives

- Tools: `echo(message)` and `current_time()`.
- Resource: `example://welcome`.
- Prompt: `greet(name)`.
```

- [ ] **Step 2: Delete the legacy FastAPI files**

Run: `Remove-Item -LiteralPath "main.py", "test_main.http"`

Expected: The old REST API example files no longer exist.

- [ ] **Step 3: Run the full verification suite**

Run: `uv run pytest -v`

Expected: PASS with 5 tests passed.

Run: `uv run aether-mcp-server --help`

Expected: Exit code 0 and help text listing the stdio and http transports.
