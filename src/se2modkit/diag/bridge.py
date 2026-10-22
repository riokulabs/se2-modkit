"""Client for the in-game bridge plugin (an MCP server inside a running Space Engineers 2).

Security rules:
  * the bearer token is read from the plugin's config file at call time and kept only in local variables; it is
    never printed, logged, written to a file, stored on an object or put in an exception message;
  * the plugin's config file is named only by `SE2_MCP_CFG` or the user config key `mcp_cfg`; nothing here guesses
    where it lives;
  * the endpoint must be plain http on a loopback host (`localhost`, `127.0.0.1`, `::1`), with no user info: the token
    is never sent anywhere else. Proxies and redirects are disabled for the same reason;
  * the plugin answers only "Host: localhost:<port>" requests, so the default URL uses "localhost".
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from ..env.config import ConfigError, load_user_config
from ..env.core import SE2Env

LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_URL = "http://localhost:6789/"
CONFIG_KEY = "mcp_cfg"
_TOKEN_RE = re.compile(r"<SecretKey>([^<]+)</SecretKey>")


class GameUnavailable(RuntimeError):
    """The game, the plugin or its config is not reachable. The message never contains the token."""


def check_url(url: str) -> str:
    """Return `url` if it is plain http to a loopback host; raise `GameUnavailable` otherwise."""
    bad = GameUnavailable("refusing a non-loopback bridge endpoint (the token may only go to localhost, 127.0.0.1 "
                          "or ::1 over http)")
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
        raise bad
    try:
        u = urllib.parse.urlsplit(url)
        u.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        raise bad from None
    if u.scheme != "http" or "@" in u.netloc or (u.hostname or "") not in LOOPBACK:
        raise bad
    return url


def read_token(cfg: Path) -> str:
    """The plugin's `SecretKey`. Errors name the file, never its contents."""
    try:
        text = Path(cfg).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise GameUnavailable(f"cannot read the bridge config {cfg}: {e.strerror}") from None
    m = _TOKEN_RE.search(text)
    if not m or not m.group(1).strip():
        raise GameUnavailable(f"no SecretKey in the bridge config {cfg}")
    return m.group(1).strip()


def config_path(env: SE2Env | None = None, *, environ: Mapping[str, str] | None = None,
                config: Mapping[str, object] | None = None) -> Path:
    """The plugin config file: `SE2_MCP_CFG`, else user config `mcp_cfg`; else one plain sentence."""
    environ = os.environ if environ is None else environ
    if environ.get("SE2_MCP_CFG"):
        return Path(environ["SE2_MCP_CFG"]).expanduser()
    if config is None:
        try:
            config = load_user_config(env.config_dir / "config.toml" if env else None, environ=environ)
        except ConfigError as e:
            raise GameUnavailable(str(e)) from None
    if config.get(CONFIG_KEY):
        return Path(str(config[CONFIG_KEY])).expanduser()
    raise GameUnavailable(f"the bridge config is not set; set SE2_MCP_CFG or config {CONFIG_KEY} to the bridge "
                          "plugin's config file")


def token(env: SE2Env | None = None, *, environ: Mapping[str, str] | None = None,
          config: Mapping[str, object] | None = None) -> str:
    """The bearer token from the configured plugin config. Never log or print the result."""
    return read_token(config_path(env, environ=environ, config=config))


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "<redacted>") if secret else text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - never follow: keeps the token local
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _post(url: str, payload: dict, tok: str, session: str | None, timeout: float) -> tuple[str | None, dict]:
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return r.headers.get("Mcp-Session-Id"), json.loads(body.strip() or "{}")
    except urllib.error.HTTPError as e:
        raise GameUnavailable(f"the bridge answered HTTP {e.code}") from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise GameUnavailable(f"the bridge is not reachable at {url} ({redact(str(reason), tok)})") from None
    except json.JSONDecodeError:
        raise GameUnavailable("the bridge sent a reply that is not JSON") from None


def _redact_tree(obj: object, tok: str) -> object:
    if isinstance(obj, str):
        return redact(obj, tok)
    if isinstance(obj, list):
        return [_redact_tree(x, tok) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_tree(v, tok) for k, v in obj.items()}
    return obj


def call(tool: str, args: dict, *, url: str | None = None, cfg: Path | None = None, env: SE2Env | None = None,
         timeout: float = 120.0, client_name: str = "se2modkit") -> dict:
    """Call one MCP tool on the bridge and return its `result` (any echo of the token replaced by `<redacted>`).

    `url` defaults to `SE2_MCP_URL`, then http://localhost:6789/; a non-loopback URL is refused before the token is
    read. `cfg` overrides the plugin config file (else `token(env)` rules).
    """
    target = check_url(url or os.environ.get("SE2_MCP_URL") or DEFAULT_URL)
    tok = read_token(cfg) if cfg is not None else token(env)
    sid, init = _post(target, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": client_name, "version": "1"}}}, tok, None, timeout)
    if "error" in init:
        raise GameUnavailable(redact(f"initialize failed: {init['error'].get('message')}", tok))
    _, res = _post(target, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": tool, "arguments": args}}, tok, sid, timeout)
    if "error" in res:
        raise GameUnavailable(redact(f"{tool} failed: {res['error'].get('message')}", tok))
    return _redact_tree(res.get("result", {}), tok)
