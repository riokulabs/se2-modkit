import logging

import pytest
from diagfix import TOKEN

from se2modkit.diag import bridge, client, locations


@pytest.mark.parametrize("url", [
    "http://example.com:6789/", "http://localhost.evil.com/", "http://evil.localhost.example/",
    "https://localhost:6789/", "ftp://localhost/", "http://10.0.0.5:6789/", "http://0.0.0.0:6789/",
    "http://127.1:6789/", "http://2130706433/", "http://127.0.0.2:6789/", "http://[::ffff:127.0.0.1]:6789/",
    "http://[::ffff:7f00:1]/", "http://[0:0:0:0:0:0:0:1]/", "http://localhost./",
    "http://localhost@evil.com/", "http://localhost:6789@evil.com/", "http://evil.com\\@localhost/",
    "http://user:pw@localhost:6789/", "http://localhost:notaport/", "http://local host/", " http://localhost/",
    "localhost:6789"])
def test_bridge_refuses_remote_url(url, tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("contacted the network")
    monkeypatch.setattr(bridge, "_post", no_network)
    with pytest.raises(bridge.GameUnavailable) as e:
        bridge.call("execute_code", {"code": "1"}, url=url, cfg=tmp_path / "missing.cfg")
    assert "non-loopback" in str(e.value)           # refused before the config is even read


@pytest.mark.parametrize("url", ["http://localhost:6789/", "http://127.0.0.1:6789/", "http://[::1]:6789/",
                                 "http://LOCALHOST:6789/"])
def test_bridge_accepts_loopback(url):
    assert bridge.check_url(url) == url


def test_bridge_ignores_proxies_and_redirects(fake_mcp, mcp_cfg, monkeypatch):
    handlers = {type(h).__name__ for h in bridge._OPENER.handlers}
    assert "_NoRedirect" in handlers and "HTTPRedirectHandler" not in handlers
    assert all(not getattr(h, "proxies", None) for h in bridge._OPENER.handlers)
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9/")   # a proxy would fail: the call must go direct
    monkeypatch.setenv("no_proxy", "")
    assert bridge.call("take_screenshot", {}, url=fake_mcp.url, cfg=mcp_cfg)["content"]


def test_bridge_token_from_env_or_config(mcp_cfg, tmp_path):
    e = locations.env()
    assert bridge.token(e, environ={"SE2_MCP_CFG": str(mcp_cfg)}) == TOKEN
    assert bridge.token(e, environ={}, config={"mcp_cfg": str(mcp_cfg)}) == TOKEN
    with pytest.raises(bridge.GameUnavailable) as err:
        bridge.token(e, environ={}, config={})
    msg = str(err.value)
    assert "SE2_MCP_CFG" in msg and "mcp_cfg" in msg and "\n" not in msg


def test_bridge_call_roundtrip(fake_mcp, mcp_cfg):
    res = bridge.call("take_screenshot", {}, url=fake_mcp.url, cfg=mcp_cfg)
    assert res["content"][0]["type"] == "text"
    assert fake_mcp.calls[1]["body"]["params"]["name"] == "take_screenshot"


def test_bridge_token_never_in_repr_or_logs(fake_mcp, mcp_cfg, tmp_path, monkeypatch, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SE2_MCP_CFG", str(mcp_cfg))
    seen = []
    fake_mcp.reply_text = f"echo {TOKEN} back"
    res = bridge.call("execute_code", {"code": "x"}, url=fake_mcp.url)
    seen.append(repr(res))
    c = client.GameClient(url=fake_mcp.url)
    seen += [repr(c), str(c), repr(vars(c)), repr(c.execute("x"))]
    for status in (401, 500):
        fake_mcp.status = status
        with pytest.raises(bridge.GameUnavailable) as e:
            bridge.call("execute_code", {"code": "x"}, url=fake_mcp.url)
        seen += [str(e.value), repr(e.value), repr(e.value.__cause__), repr(e.value.__context__)]
    with pytest.raises(bridge.GameUnavailable) as e:
        bridge.call("execute_code", {}, url="http://localhost:9/", timeout=2)
    seen += [str(e.value), repr(e.value)]
    out = capsys.readouterr()
    seen += [out.out, out.err, caplog.text]
    for s in seen:
        assert TOKEN not in s
    for p in tmp_path.rglob("*"):
        if p.is_file() and p != mcp_cfg:
            assert TOKEN.encode() not in p.read_bytes(), p
