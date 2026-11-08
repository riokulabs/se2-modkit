"""`se2mod mcp` over the protocol: the tools list with their hints, a call's structured result, and a real stdio
session with the command (needs the optional `mcp` package; skipped without it)."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

pytest.importorskip("mcp.server.mcpserver", reason="the optional mcp package (2.x) is not installed")

from mcp.client import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from se2modkit.mcp.server import build_server  # noqa: E402
from se2modkit.mcp.tools import Toolbox, tool_names  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "src"


def _env(tmp_path):
    return SimpleNamespace(cache_dir=tmp_path / "cache", platform="linux", blender=None, sdk_assets=None)


def test_in_process_session_lists_tools_and_calls_one(tmp_path):
    async def go():
        async with Client(build_server(Toolbox(_env(tmp_path)))) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            status = await c.call_tool("queue_status", {})
            bad = await c.call_tool("defs", {"action": "nope"})
            return tools, status, bad
    tools, status, bad = anyio.run(go)
    assert list(tools) == tool_names()
    assert tools["defs"].annotations.read_only_hint is True
    assert tools["queue_submit"].annotations.destructive_hint is True
    assert tools["look"].annotations.read_only_hint is False and tools["look"].annotations.destructive_hint is False
    assert tools["look"].input_schema["properties"]["stage"]["enum"] == ["look", "concept", "fix"]
    assert tools["look"].input_schema["required"] == ["source", "block"]
    assert status.is_error is False and status.structured_content["jobs"] == []
    assert status.structured_content["ok"] is True
    assert bad.is_error is True


def test_the_command_serves_over_stdio(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(SRC), "XDG_CACHE_HOME": str(tmp_path / "xdg"),
           "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "LOCALAPPDATA": str(tmp_path / "local")}
    env.pop("SE2MODKIT_CONFIG", None)
    params = StdioServerParameters(command=sys.executable, args=["-c", "from se2modkit.cli import main; "
                                                                       "raise SystemExit(main(['mcp']))"],
                                   env=env, cwd=str(tmp_path))

    async def go():
        with anyio.fail_after(60):
            async with Client(params) as c:
                names = [t.name for t in (await c.list_tools()).tools]
                status = await c.call_tool("queue_status", {})
                return names, status
    names, status = anyio.run(go)
    assert names == tool_names()
    assert status.structured_content["ok"] is True and status.structured_content["jobs"] == []
    assert str(tmp_path) in status.structured_content["runner_log"]
