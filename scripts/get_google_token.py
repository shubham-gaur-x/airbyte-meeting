#!/usr/bin/env python3
"""
Google OAuth2 refresh token helper.

Run this ONCE after creating an OAuth 2.0 client ID in Google Cloud Console.
It opens your browser, handles the auth flow, and writes GOOGLE_REFRESH_TOKEN
(and GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) into your .env file automatically.

Usage:
    python scripts/get_google_token.py

Prerequisites (one-time, ~2 minutes):
    1. Go to console.cloud.google.com → APIs & Services → Credentials
    2. Create Credentials → OAuth 2.0 Client IDs → Desktop App
    3. Name it "meeting-memory-graph", click Create
    4. Download the JSON → copy Client ID and Client Secret into .env:
           GOOGLE_CLIENT_ID=<your-client-id>
           GOOGLE_CLIENT_SECRET=<your-client-secret>
    5. Run this script → browser opens → approve → done.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "email",
    "profile",
]
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
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
<h2>&#10003; Authentication successful!</h2>
<p>You can close this tab and return to the terminal.</p>
</body></html>""")
        else:
            _CallbackHandler.error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Authentication failed. Check the terminal.</body></html>")

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    env = _load_env()
    client_id = env.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = env.get("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("""
ERROR: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set in .env

To get them (one-time, ~2 minutes):
  1. Go to https://console.cloud.google.com/apis/credentials?project=airbyte-meeting
  2. Click "Create Credentials" → "OAuth 2.0 Client IDs"
  3. Application type: Desktop App
  4. Name: meeting-memory-graph → Create
  5. Copy the Client ID and Client Secret into your .env:
         GOOGLE_CLIENT_ID=<id>
         GOOGLE_CLIENT_SECRET=<secret>
  6. Re-run this script.
""")
        sys.exit(1)

    port = _free_port()
    redirect_uri = f"http://localhost:{port}/callback"

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{AUTH_ENDPOINT}?{urlencode(auth_params)}"

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 300  # 5 minutes

    print(f"\nOpening browser for Google authentication...")
    print(f"Auth URL:\n  {auth_url}\n")
    print(f"If the browser doesn't open automatically, copy the URL above into your browser.\n")
    import subprocess
    subprocess.Popen(["open", auth_url])

    server.handle_request()

    if _CallbackHandler.error:
        print(f"ERROR: OAuth flow failed: {_CallbackHandler.error}")
        sys.exit(1)
    if not _CallbackHandler.code:
        print("ERROR: No authorization code received (timed out after 5 minutes)")
        sys.exit(1)

    print("Exchanging authorization code for refresh token...")
    r = httpx.post(TOKEN_ENDPOINT, data={
        "code": _CallbackHandler.code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=30)

    if r.status_code != 200:
        print(f"ERROR: Token exchange failed: {r.text}")
        sys.exit(1)

    tokens = r.json()
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh_token in response. Make sure 'access_type=offline' and 'prompt=consent' are set.")
        sys.exit(1)

    _update_env({"GOOGLE_REFRESH_TOKEN": refresh_token})
    print(f"\n{'='*50}")
    print("  Google OAuth2 setup complete!")
    print(f"  Refresh token written to .env")
    print(f"  Scopes: Gmail (read) + Calendar (read)")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
