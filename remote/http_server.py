#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
REMOTE = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from kicad_mcp.server import create_server
from remote.google_oauth import GoogleOAuthProvider

def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def _wait_for_cloudflare_url(proc: subprocess.Popen, timeout_s: float = 45.0) -> str:
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + timeout_s
    buf = ""
    assert proc.stderr is not None
    while time.time() < deadline:
        if proc.poll() is not None:
            rest = proc.stderr.read() or ""
            raise RuntimeError(f"cloudflared exited {proc.returncode}: {rest[-1500:]}")
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue
        buf += line
        match = pattern.search(line) or pattern.search(buf)
        if match:
            return match.group(0).rstrip("/")
    raise RuntimeError("Timed out waiting for a trycloudflare.com URL. Is cloudflared installed?")

def _start_tunnel(port: int) -> tuple[subprocess.Popen, str]:
    try:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as e:
        raise SystemExit(
            "cloudflared is not installed. Run: brew install cloudflared"
        ) from e
    url = _wait_for_cloudflare_url(proc)
    (REMOTE / ".public_url").write_text(url + "\n", encoding="utf-8")
    return proc, url

def build_server(public_base: str) -> tuple[object, GoogleOAuthProvider]:
    google_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    google_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not google_id or not google_secret:
        raise SystemExit(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in remote/.env"
        )

    allowed = [
        part.strip()
        for part in os.environ.get("ALLOWED_GOOGLE_EMAILS", "").split(",")
        if part.strip()
    ]
    provider = GoogleOAuthProvider(
        google_client_id=google_id,
        google_client_secret=google_secret,
        public_base_url=public_base,
        allowed_emails=allowed or None,
    )
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(public_base),
        resource_server_url=AnyHttpUrl(public_base.rstrip("/") + "/mcp"),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            default_scopes=["kicad"],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=None,
    )
    mcp = create_server(auth=auth, auth_server_provider=provider)

    @mcp.custom_route("/oauth/callback", methods=["GET"], name="google_oauth_callback")
    async def google_callback(request: Request) -> Response:
        return await provider.handle_google_callback(request)

    @mcp.custom_route("/health", methods=["GET"], name="health")
    async def health(_request: Request) -> Response:
        return JSONResponse(
            {
                "ok": True,
                "mcp": public_base.rstrip("/") + "/mcp",
                "google_redirect_uri": provider.google_redirect_uri,
            }
        )

    return mcp, provider

def main() -> None:
    load_dotenv(REMOTE / ".env")
    parser = argparse.ArgumentParser(description="KiCad MCP over Streamable HTTP + Google OAuth")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3001")))
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="Start a Cloudflare quick tunnel and use that https URL as the OAuth issuer",
    )
    parser.add_argument(
        "--public-url",
        default=os.environ.get("PUBLIC_BASE_URL", ""),
        help="Public https base URL (Grok connector). Required unless --tunnel.",
    )
    args = parser.parse_args()

    tunnel_proc = None
    public = (args.public_url or "").rstrip("/")
    if args.tunnel:
        tunnel_proc, public = _start_tunnel(args.port)
        print(f"Tunnel: {public}")
    if not public:
        public = f"http://127.0.0.1:{args.port}"
        print("No PUBLIC_BASE_URL / --tunnel: using localhost (Grok.com cannot reach this).")

    mcp, provider = build_server(public)
    parsed = urlparse(public)
    host_header = parsed.netloc or f"127.0.0.1:{args.port}"
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", host_header],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            public,
        ],
    )

    print("KiCad remote MCP")
    print(f"  local:  http://{args.host}:{args.port}/mcp")
    print(f"  public: {public}/mcp")
    print(f"  add this exact URI in Google Cloud → Credentials → Authorized redirect URIs:")
    print(f"    {provider.google_redirect_uri}")
    print("Then in Grok: Connectors → Custom → URL above → finish Google login.")
    try:
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
            transport_security=security,
        )
    finally:
        if tunnel_proc is not None and tunnel_proc.poll() is None:
            tunnel_proc.terminate()

if __name__ == "__main__":
    main()
