"""Game client for the in-game bridge plugin (execute_code, take_screenshot), built on `bridge.call`.

The token rules live in `bridge`: it is read at call time, never kept on this object, never printed or logged.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from . import bridge
from . import locations as paths
from .bridge import GameUnavailable, check_url, read_token, redact

__all__ = ["GameClient", "GameUnavailable", "ToolResult", "check_url", "read_token", "redact"]


@dataclass
class ToolResult:
    texts: list
    images: list          # base64-decoded bytes
    is_error: bool


class GameClient:
    def __init__(self, url: str | None = None, cfg: Path | None = None, timeout: float = 120.0):
        self.url = check_url(url or paths.mcp_url())
        self.cfg = cfg
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"GameClient(url={self.url!r}, timeout={self.timeout})"

    def call(self, tool: str, args: dict) -> ToolResult:
        result = bridge.call(tool, args, url=self.url, cfg=self.cfg, env=None if self.cfg else paths.env(),
                             timeout=self.timeout, client_name="se2diag")
        texts, images = [], []
        for c in result.get("content", []):
            if c.get("type") == "text":
                texts.append(c.get("text", ""))
            elif c.get("type") == "image":
                images.append(base64.b64decode(c.get("data", "")))
        return ToolResult(texts, images, bool(result.get("isError")))

    def execute(self, code: str, target: str = "main") -> ToolResult:
        return self.call("execute_code", {"code": code, "target": target,
                                          "usings": ["System.Linq", "System.Reflection", "System.Collections.Generic"]})

    def screenshot(self) -> ToolResult:
        return self.call("take_screenshot", {})
