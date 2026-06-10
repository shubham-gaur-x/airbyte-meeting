#!/usr/bin/env python3
"""
Slack OAuth refresh token helper.

Run this ONCE after creating a Slack app. It opens a browser, handles the
OAuth flow, and writes SLACK_REFRESH_TOKEN (and optionally SLACK_CLIENT_ID /
SLACK_CLIENT_SECRET) to .env.

Usage:
    python scripts/get_slack_token.py

Prerequisites (one-time, ~3 minutes):
    1. Go to https://api.slack.com/apps → "Create New App" → "From scratch"
    2. Name: "meeting-memory-graph", choose your workspace → Create App
    3. OAuth & Permissions → Redirect URLs → Add: http://localhost:<auto-port>/callback
       (the script will tell you the port before opening the browser)
    4. Scopes → Bot Token Scopes → Add: channels:history, channels:read,
       groups:history, groups:read, im:history, mpim:history, users:read,
       users:read.email
    5. Basic Information → App Credentials → copy Client ID and Client Secret:
           SLACK_CLIENT_ID=<id>
           SLACK_CLIENT_SECRET=<secret>
    6. Run this script → browser opens → Approve → done.
"""

from __future__ import annotations

import os
import socket
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTH_ENDPOINT = "https://slack.com/oauth/v2/authorize"
TOKEN_ENDPOINT = "https://slack.com/api/oauth.v2.access"
SCOPES = "channels:history,channels:read,groups:history,groups:read,im:history,mpim:history,users:read,users:read.email"
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _update_env(updates: dict[str, str]) -> None:
    lines: list[str] = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            lines = f.readlines()

    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    with open(ENV_PATH, "w") as f:
        f.writelines(new_lines)
    print(f"  ✓ Updated .env with {', '.join(updates.keys())}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _CallbackHandler.code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
<html><body style="font-family:sans-serif;max-width:500px;margin:60px auto;text-align:center">
<h2>&#10003; Slack authentication successful!</h2>
<p>You can close this tab and return to the terminal.</p>
</body></html>""")
        else:
            _CallbackHandler.error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Authentication failed.</body></html>")

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    env = _load_env()
    client_id = env.get("SLACK_CLIENT_ID", "").strip()
    client_secret = env.get("SLACK_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("""
ERROR: SLACK_CLIENT_ID and SLACK_CLIENT_SECRET are not set in .env

To get them (one-time, ~3 minutes):
  1. Go to https://api.slack.com/apps → "Create New App" → "From scratch"
  2. Name: "meeting-memory-graph", pick your workspace → Create App
  3. OAuth & Permissions → Bot Token Scopes → Add:
       channels:history, channels:read, groups:history, groups:read,
       im:history, mpim:history, users:read, users:read.email
  4. Basic Information → App Credentials → copy Client ID and Client Secret:
         SLACK_CLIENT_ID=<id>
         SLACK_CLIENT_SECRET=<secret>
  5. Re-run this script (it will print the redirect URL to add to Slack).
""")
        sys.exit(1)

    port = _free_port()
    redirect_uri = f"http://localhost:{port}/callback"

    print(f"\nIMPORTANT: Make sure this URL is in your Slack app's Redirect URLs:")
    print(f"  {redirect_uri}")
    print(f"\n  → Slack App settings → OAuth & Permissions → Redirect URLs → Add URL above\n")
    input("Press Enter once you've added the redirect URL...")

    auth_params = {
        "client_id": client_id,
        "scope": SCOPES,
        "redirect_uri": redirect_uri,
    }
    auth_url = f"{AUTH_ENDPOINT}?{urlencode(auth_params)}"

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 120

    print(f"\nOpening browser for Slack authentication...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    server.handle_request()

    if _CallbackHandler.error:
        print(f"ERROR: OAuth flow failed: {_CallbackHandler.error}")
        sys.exit(1)
    if not _CallbackHandler.code:
        print("ERROR: No authorization code received (timed out after 120s)")
        sys.exit(1)

    print("Exchanging authorization code for tokens...")
    r = httpx.post(TOKEN_ENDPOINT, data={
        "code": _CallbackHandler.code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=30)

    data = r.json()
    if not data.get("ok"):
        print(f"ERROR: Token exchange failed: {data.get('error', 'unknown')}")
        sys.exit(1)

    # Airbyte Slack source expects a refresh token (from the authed_user section)
    refresh_token = data.get("authed_user", {}).get("refresh_token") or data.get("refresh_token", "")
    access_token = data.get("access_token", "")

    if not refresh_token and not access_token:
        print(f"ERROR: No token in response: {data}")
        sys.exit(1)

    # Slack v2 OAuth returns access_token (bot) — store as refresh token for Airbyte
    token_to_store = refresh_token or access_token
    _update_env({"SLACK_REFRESH_TOKEN": token_to_store})

    print(f"\n{'='*50}")
    print("  Slack OAuth2 setup complete!")
    print(f"  Token written to .env as SLACK_REFRESH_TOKEN")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
