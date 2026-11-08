"""Build and run the MCP server around `tools.Toolbox`. The `mcp` package (2.x) is imported only here, and only when
the server is built, so the rest of the modkit never needs it."""

from __future__ import annotations

from .tools import Toolbox, ToolSpec

INSTRUCTIONS = (
    "Space Engineers 2 mod tooling (se2-modkit). Tools return JSON with file paths, not terminal text; every result "
    "has ok (false with an error line when the call could not do its work) and, for checks, code (the matching "
    "se2mod command's exit code). Renders, describe and ship checks run as jobs on the shared GPU job queue, one "
    "heavy job at a time; never start Blender any other way than through these tools or queue_submit. Read-only "
    "tools carry readOnlyHint."
)

MISSING = ("the MCP server needs the `mcp` package (version 2 or later): pip install 'se2-modkit[mcp]' "
           "(or pip install 'mcp>=2')")


class McpMissing(RuntimeError):
    """The optional `mcp` package is not installed (or is a 1.x release)."""


def annotations(spec: ToolSpec) -> dict:
    """The MCP tool annotations for a spec (as keyword arguments of `mcp.types.ToolAnnotations`)."""
    return {"title": spec.title, "readOnlyHint": spec.read_only,
            "destructiveHint": spec.destructive if not spec.read_only else False,
            "idempotentHint": spec.idempotent, "openWorldHint": False}


def build_server(toolbox: Toolbox | None = None):
    """An `MCPServer` serving every tool of `toolbox` (default: one over the discovered environment)."""
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.types import ToolAnnotations
    except ImportError as e:
        raise McpMissing(MISSING) from e
    from .. import __version__
    toolbox = toolbox or Toolbox()
    server = MCPServer("se2modkit", instructions=INSTRUCTIONS, version=__version__)
    for spec in toolbox.specs():
        server.add_tool(spec.fn, name=spec.name, title=spec.title, description=spec.description,
                        annotations=ToolAnnotations(**annotations(spec)), structured_output=True)
    return server


def run_stdio(toolbox: Toolbox | None = None) -> None:
    """Serve over stdin and stdout until the client hangs up. The transport keeps stdout for the protocol and points
    everything else printed (here and by the jobs' helpers) at stderr."""
    build_server(toolbox).run("stdio")
