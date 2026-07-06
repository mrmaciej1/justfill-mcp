"""`justfill-mcp login` loopback callback — the nonce is the security boundary.

A drive-by web page (or any local process) can reach 127.0.0.1:PORT while the
listener is up; without the state nonce it could plant a foreign API key and
silently route the user's documents to an attacker's account. These tests run
the real main() flow against the real HTTP listener.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from justfill_mcp import login


def _run_login(monkeypatch, tmp_path, attack):
    """Run login.main() with a fake browser; `attack(url)` plays the browser."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(login, "LOGIN_TIMEOUT_S", 5)
    captured: dict = {}

    def fake_open(url):
        captured["url"] = url
        threading.Thread(target=attack, args=(url,), daemon=True).start()
        return True

    monkeypatch.setattr(login.webbrowser, "open", fake_open)
    rc = login.main()
    return rc, captured.get("url", "")


def _params(url: str) -> dict:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def _get(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_login_happy_path_requires_and_accepts_nonce(monkeypatch, tmp_path):
    def browser(url):
        p = _params(url)
        assert p.get("state"), "authorize URL must carry the anti-fixation nonce"
        _get(f"http://127.0.0.1:{p['port']}/callback?key=jf_live_testkey&state={p['state']}")

    rc, url = _run_login(monkeypatch, tmp_path, browser)
    assert rc == 0
    saved = json.loads((tmp_path / "justfill" / "credentials.json").read_text())
    assert saved["api_key"] == "jf_live_testkey"


def test_login_rejects_callback_without_nonce(monkeypatch, tmp_path):
    """Key fixation attempt: a request without the nonce must be 403'd and
    must NOT complete the flow or store the attacker's key."""
    statuses: list[int] = []

    def attacker(url):
        p = _params(url)
        base = f"http://127.0.0.1:{p['port']}/callback"
        statuses.append(_get(f"{base}?key=jf_live_attacker"))
        statuses.append(_get(f"{base}?key=jf_live_attacker&state=wrong"))

    rc, _ = _run_login(monkeypatch, tmp_path, attacker)
    assert rc == 1, "flow must time out, not accept the planted key"
    assert statuses == [403, 403]
    assert not (tmp_path / "justfill" / "credentials.json").exists()
