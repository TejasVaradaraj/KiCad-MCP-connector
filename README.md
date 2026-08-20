# KiCad MCP Server

Lets an LLM talk to a KiCad PCB that is already open on your machine.

KiCad does not ship a chatbot. Its Python API used to be SWIG/`pcbnew`, which broke between versions and only ran inside KiCad. The newer IPC API (`kicad-python` / `kipy`) is a socket to a running KiCad. This repo wraps that socket as MCP tools so Grok (or anything else that speaks MCP) can inspect the board, do light edits, and kick off Gerber exports.

If KiCad is closed, nothing here works. The server does not load `.kicad_pcb` files by itself.

## What it can and cannot do

Tested against KiCad **10.0.5** and kicad-python **0.7.1**. A version warning (KiCad slightly newer than the bindings) is normal and is not a disconnect.

Works well:

- Connection check, list open documents, board/project info
- Footprints, nets, tracks, vias, zones, layers, stackup, title block, text
- Unit conversion and layer-name helpers (these do not need a board)
- Save-as, and the usual create/move/lock tools if KiCad implements them on your build
- Gerbers / drill / PDF / position files via **kicad-cli** (the IPC API on KiCad 9/10 has no plotting)

Does not work on KiCad 10, or only barely:

- Opening or closing documents from the server — that needs KiCad 11 headless (`kicad-cli api-server`). Open files in the GUI.
- Schematic tools — `get_schematic` is not in this kicad-python.
- Anything against a board that is not the one currently open in Pcbnew.

Layer IDs are IPC values, not the numbers in the `.kicad_pcb` file. **F.Cu is 3**, B.Cu is 34, 0 is “unknown”.

## How it is put together

```
LLM (Grok.com, Grok Build, Inspector, …)
        │
        │  stdio  or  https://…/mcp
        ▼
this repo (Python 3.12, mcp, pydantic)
        │
        │  unix socket  /tmp/kicad/api.sock  or  api-<pid>.sock
        ▼
KiCad GUI  (API enabled, a .kicad_pcb open)
        │
        └─ exports call kicad-cli on a snapshot of that board
```

- `kicad_mcp/` — connection + tools. This is the actual product.
- `server.py` — stdio entry point (`uv run kicad-mcp` is the same thing).
- `remote/` — HTTP + Google login + Cloudflare tunnel so Grok.com can reach your Mac. Still runs locally; KiCad never moves to the cloud.
- `scripts/smoke_tools.py` — calls every tool through MCP Inspector.

Stack: Python 3.12, `uv`, `mcp` 2.0, `kicad-python`, `pydantic`. Remote extra: Streamable HTTP, Google OAuth, `cloudflared`.

## Install

```bash
git clone https://github.com/TejasVaradaraj/KiCad_MCP_Server.git
cd KiCad_MCP_Server
uv sync
```

Needs KiCad 10+ with the API on. Do not point KiCad’s “Python interpreter” at this venv — that setting is only for KiCad plugins.

## Use it locally (stdio)

1. Start **one** KiCad.
2. Preferences → Plugins → **Enable KiCad API**.
3. Open the PCB you care about (Pcbnew, not just the schematic).
4. Run:

```bash
uv run kicad-mcp
```

Check the connection:

```bash
uv run python -c "
from kicad_mcp.connection import reset_connection, get_kicad
reset_connection()
k = get_kicad(); k.ping()
b = k.get_board()
print('board', None if b is None else b.name)
print('path', None if b is None else b.get_project().path)
"
```

You want the printed path to be your project folder, not some other board you left open.

Grok Build on this Mac can spawn that command from `~/.grok/config.toml`:

```toml
[mcp_servers.kicad]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/KiCad_MCP_Server", "kicad-mcp"]
enabled = true
```

Smoke (KiCad still open):

```bash
uv run python scripts/smoke_tools.py
```

`--unsafe` also hits save/move/export. Don’t do that on a board you have not copied.

## Use it from Grok.com (custom connector)

Grok.com cannot see `localhost`. You run an HTTP server on the Mac and punch a hole with Cloudflare.

1. `brew install cloudflared`
2. Create a Google Cloud **OAuth client, type Web application**. Put the client id and secret in `remote/.env` (see `remote/.env.example`). Do not commit `.env`.
3. Optional: `ALLOWED_GOOGLE_EMAILS=you@gmail.com` so only you can approve.
4. KiCad open, API on, PCB open.
5. From the repo:

```bash
./remote/tunnel.sh
```

6. The process prints two URLs:
   - **MCP:** `https://something.trycloudflare.com/mcp` — this is what Grok wants.
   - **Google redirect:** `https://something.trycloudflare.com/oauth/callback` — add this exact string under Authorized redirect URIs on the Google client, then save.
7. On [grok.com/connectors](https://grok.com/connectors) → New connector → Custom. Paste the **MCP** URL. Finish Google login.

Keep three things running while you chat: KiCad, `./remote/tunnel.sh`, and the tunnel it started. Stop the tunnel when you are done; the URL is public.

Cloudflare quick tunnels change hostname every restart. When that happens, add the new redirect URI in Google and update the connector.

A free Claude plan usually cannot add custom connectors. Local Grok Build does not need any of this HTTP stuff.

## Env (optional)

| Variable | Meaning |
|---|---|
| `KICAD_MCP_SOCKET` | Force the IPC URL, e.g. `ipc:///tmp/kicad/api-83532.sock` |
| `KICAD_MCP_TOKEN` | KiCad API token, if you set one |
| `KICAD_MCP_TIMEOUT_MS` | Default 5000 |

If `api.sock` is already taken, KiCad names the socket `api-<pid>.sock`. Preferences shows “Listening at …”. The server tries the default and then any `api-*.sock` it finds.

## Layout

```
server.py                 stdio
kicad_mcp/                tools + KiCad socket
remote/                   HTTP / Google / tunnel
scripts/smoke_tools.py
tests/fixtures/           dummy args + a tiny demo pcb
```
