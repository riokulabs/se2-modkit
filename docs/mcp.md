# MCP server

`se2mod mcp` serves the commands an agent runs most as [Model Context Protocol](https://modelcontextprotocol.io)
tools over stdio. Each tool calls the same Python functions as its command and returns JSON with file paths instead of
terminal text: a look set's folder, sheet and report, a describe report as data, each block's ship-check report.

It needs the optional `mcp` package (version 2 or later):

```bash
pip install "se2-modkit[mcp]"
se2mod mcp --list      # the tools and whether each one only reads
```

## Client config

For a client that starts stdio servers from a JSON config (such as an `.mcp.json`):

```json
{
  "mcpServers": {
    "se2modkit": {
      "command": "se2mod",
      "args": ["mcp"]
    }
  }
}
```

Use the full path of `se2mod` in a virtual environment if it is not on the client's `PATH`. The server takes the usual
environment discovery (`--sdk`, `--blender`, the user config, `SE2_*` variables), so flags such as
`["mcp", "--blender", "/path/to/blender"]` work too.

## Tools

| Tool | Reads only | Does |
|---|---|---|
| `look` | no | a look set of a block through the job queue (as `se2mod look`); returns `set_dir`, `sheet`, `report_path`, `not_ready` and `geocheck` |
| `look_finish` | no | checks a set whose render ended (after `look` with `wait: false` and `queue_wait`): sheet and report |
| `geocheck` | yes | a look set's geometry check results (`geocheck.json`) and its `not_ready` list |
| `describe` | no | a block model as numbers through the queue (as `se2mod describe`); returns the report as data |
| `ship_check` | no | the check before an export (as `se2mod ship check`); returns each block's report and its `ship.json` path |
| `queue_status` | yes | the runner and each job, with its log path |
| `queue_submit` | no, and may delete | queues any command (as `se2mod queue submit`); the way to run Blender |
| `queue_wait` | yes | waits for a job; its exit code, state and the end of its log |
| `defs` | yes | `types`, `type`, `guid`, `show` and `grep` over compiled definitions (as `se2mod defs`) |
| `chain` | yes | `show`, `resolve`, `lineage` and `find` over the Mod SDK sources (as `se2mod chain`); the index is kept between calls |
| `deps_check` | yes | re-checks the recorded game dependencies (as `se2mod deps check`); summary and findings |

Each tool carries the MCP hints `readOnlyHint`, `destructiveHint` and `idempotentHint`, so a client can allow the
read-only ones without asking. Every result has `ok`; a tool that could not do its work returns `ok: false` with an
`error` line, and the checks also return `code`, the matching command's exit code.

Renders, describe and ship checks run as jobs on the shared GPU queue ([render-queue.md](render-queue.md)), never as a
Blender the server starts itself. While serving, stdout carries only the protocol; anything else printed goes to stderr.
