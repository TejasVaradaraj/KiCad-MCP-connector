# Remote front door

HTTP + Google login so Grok.com can call the same tools as `uv run kicad-mcp`. KiCad still has to be running on this Mac.

```bash
brew install cloudflared
# fill remote/.env from .env.example
./remote/tunnel.sh
```

Paste `https://<host>/mcp` into Grok → Connectors → Custom. Add `https://<host>/oauth/callback` as a Google redirect URI first.

Quick tunnels change URL every restart. Local-only (no Grok.com): `./remote/run.sh`.
