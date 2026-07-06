"""`justfill-mcp login` — browser-based authorization (no password in config).

Starts a one-shot loopback HTTP listener, opens https://justfill.app/authorize
in the browser, the logged-in user clicks Authorize, and the freshly minted
API key arrives at http://127.0.0.1:PORT/callback?key=...&state=<nonce>. The
nonce is minted here and must round-trip through the authorize page — without
it, any local process or drive-by web page spraying loopback ports could plant
a foreign key (key fixation) while the listener is up. The key is stored in
~/.config/justfill/credentials.json (0600); the API client picks it up
automatically when JUSTFILL_API_KEY is not set.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

LOGIN_TIMEOUT_S = 300

_DONE_HTML = """<!doctype html><meta charset="utf-8">
<title>JustFill — authorized</title>
<body style="font-family:system-ui;display:grid;place-items:center;height:90vh">
<div style="text-align:center">
<h2>&#9989; JustFill MCP is authorized</h2>
<p>You can close this tab and return to your terminal.</p>
</div></body>""".encode()


def credentials_path() -> Path:
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "justfill" / "credentials.json"


def load_stored_api_key() -> str | None:
    """Read the API key saved by `justfill-mcp login`, if any."""
    try:
        data = json.loads(credentials_path().read_text())
        return data.get("api_key") or None
    except Exception:
        return None


def _save(key: str, app_url: str) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"api_key": key, "app_url": app_url}, indent=1))
    path.chmod(0o600)
    return path


def main() -> int:
    app_url = (os.getenv("JUSTFILL_APP_URL") or "https://justfill.app").rstrip("/")
    nonce = secrets.token_urlsafe(16)
    result: dict = {}
    ready = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404); self.end_headers()
                return
            qs = parse_qs(parsed.query)
            key = (qs.get("key") or [""])[0]
            state = (qs.get("state") or [""])[0]
            # The nonce binds the callback to THIS login flow — a request
            # without it (e.g. a drive-by page spraying loopback ports to
            # plant a foreign key) is rejected and the wait continues.
            if not secrets.compare_digest(state, nonce):
                self.send_response(403); self.end_headers()
                return
            if key.startswith("jf_"):
                result["key"] = key
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_DONE_HTML)
            ready.set()

        def log_message(self, *args):  # silence request logging
            pass

    # OS-assigned free loopback port
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = (
        f"{app_url}/authorize?port={port}&state={nonce}"
        f"&name={quote('MCP on ' + (os.uname().nodename if hasattr(os, 'uname') else 'this computer'))}"
    )
    print("Opening your browser to authorize JustFill MCP…")
    print(f"If it does not open, visit:\n  {url}\n")
    webbrowser.open(url)

    if not ready.wait(timeout=LOGIN_TIMEOUT_S) or "key" not in result:
        server.shutdown()
        print("Authorization timed out or no key was received.", file=sys.stderr)
        print("You can also create a key manually at "
              f"{app_url}/account and set JUSTFILL_API_KEY.", file=sys.stderr)
        return 1
    server.shutdown()

    path = _save(result["key"], app_url)
    print(f"Authorized. API key saved to {path}")
    print("justfill-mcp will use it automatically — no env vars needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
