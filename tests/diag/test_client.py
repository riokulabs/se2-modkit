import pytest

from se2modkit.diag import client, probes
from diagfix import TOKEN


@pytest.mark.parametrize("url", ["http://example.com:6789/", "https://localhost:6789/", "http://10.0.0.5:6789/",
                                 "http://localhost.evil.com/", "ftp://localhost/"])
def test_refuses_non_loopback(url):
    with pytest.raises(client.GameUnavailable):
        client.check_url(url)


@pytest.mark.parametrize("url", ["http://localhost:6789/", "http://127.0.0.1:6789/", "http://[::1]:6789/"])
def test_accepts_loopback(url):
    assert client.check_url(url) == url


def test_read_token(mcp_cfg, tmp_path):
    assert client.read_token(mcp_cfg) == TOKEN
    bad = tmp_path / "bad.cfg"
    bad.write_text("<Config/>")
    with pytest.raises(client.GameUnavailable) as e:
        client.read_token(bad)
    assert TOKEN not in str(e.value)
    with pytest.raises(client.GameUnavailable):
        client.read_token(tmp_path / "missing.cfg")


def test_execute_roundtrip(fake_mcp, mcp_cfg):
    c = client.GameClient(url=fake_mcp.url, cfg=mcp_cfg)
    res = probes.run(c, "status")
    assert res.data == {"hello": 1}
    init, call = fake_mcp.calls
    assert init["body"]["method"] == "initialize"
    assert call["headers"]["Mcp-Session-Id"] == "sess-1"
    assert call["body"]["params"]["name"] == "execute_code"
    assert call["body"]["params"]["arguments"]["target"] == "main"
    assert "@@PARAMS@@" not in call["body"]["params"]["arguments"]["code"]


def test_errors_never_contain_token(fake_mcp, mcp_cfg):
    fake_mcp.status = 500
    c = client.GameClient(url=fake_mcp.url, cfg=mcp_cfg)
    with pytest.raises(client.GameUnavailable) as e:
        c.execute("Console.WriteLine(1);")
    assert TOKEN not in str(e.value)


def test_reply_text_is_redacted(fake_mcp, mcp_cfg):
    fake_mcp.reply_text = f"echo {TOKEN} back"
    c = client.GameClient(url=fake_mcp.url, cfg=mcp_cfg)
    res = c.execute("x")
    assert TOKEN not in " ".join(res.texts) and "<redacted>" in res.texts[0]


def test_unreachable_message_has_no_token(mcp_cfg):
    c = client.GameClient(url="http://localhost:9/", cfg=mcp_cfg, timeout=2)
    with pytest.raises(client.GameUnavailable) as e:
        c.execute("x")
    assert TOKEN not in str(e.value)
