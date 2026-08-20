from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from tempfile import gettempdir
from typing import Optional

logger = logging.getLogger("kicad-mcp.connection")

KICAD_SOCKET_PATH: Optional[str] = os.environ.get("KICAD_MCP_SOCKET")
KICAD_TOKEN: Optional[str] = os.environ.get("KICAD_MCP_TOKEN")
CLIENT_NAME: str = os.environ.get("KICAD_MCP_CLIENT_NAME", "kicad-mcp")
TIMEOUT_MS: int = int(os.environ.get("KICAD_MCP_TIMEOUT_MS", "5000"))
HEADLESS: bool = os.environ.get("KICAD_MCP_HEADLESS", "").lower() in ("1", "true", "yes")
KICAD_CLI_PATH: Optional[str] = os.environ.get("KICAD_MCP_CLI_PATH")
HEADLESS_FILE_PATH: Optional[str] = os.environ.get("KICAD_MCP_FILE")


class KiCadConnectionError(RuntimeError):
    pass


def _nng_url(path: str) -> str:
    path = path.strip()
    if path.startswith(("ipc://", "tcp://")):
        return path
    return f"ipc://{path}"


def _socket_candidates() -> list[str]:
    explicit = KICAD_SOCKET_PATH or os.environ.get("KICAD_API_SOCKET")
    if explicit:
        return [_nng_url(explicit)]

    found: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url not in seen:
            seen.add(url)
            found.append(url)

    for directory in (Path("/tmp/kicad"), Path(gettempdir()) / "kicad"):
        if not directory.is_dir():
            continue
        default = directory / "api.sock"
        if default.exists():
            add(_nng_url(str(default)))
        pid_socks = sorted(
            directory.glob("api-*.sock"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for sock in pid_socks:
            add(_nng_url(str(sock)))

    add("ipc:///tmp/kicad/api.sock")
    return found


def _close_quietly(kicad) -> None:
    for method in ("close", "disconnect"):
        fn = getattr(kicad, method, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
            return


def _create_kicad():
    try:
        from kipy import KiCad
    except ImportError as e:
        raise KiCadConnectionError(
            "kicad-python is not installed. Run: uv add kicad-python"
        ) from e

    import inspect

    params = inspect.signature(KiCad.__init__).parameters
    sockets = _socket_candidates() if "socket_path" in params else [None]
    errors: list[str] = []

    for socket_path in sockets:
        wanted = {
            "client_name": CLIENT_NAME,
            "timeout_ms": TIMEOUT_MS,
            "headless": HEADLESS,
            "socket_path": socket_path,
            "kicad_token": KICAD_TOKEN,
            "kicad_cli_path": KICAD_CLI_PATH,
            "file_path": HEADLESS_FILE_PATH if HEADLESS else None,
        }
        kwargs = {
            key: value
            for key, value in wanted.items()
            if value is not None and key in params
        }
        logger.info("Connecting to KiCad with kwargs=%s", list(kwargs))
        kicad = None
        try:
            kicad = KiCad(**kwargs)
            kicad.ping()
        except Exception as e:
            errors.append(f"{socket_path or 'default'}: {e}")
            if kicad is not None:
                _close_quietly(kicad)
            continue

        logger.info("KiCad connection established via %s", socket_path or "default")
        return kicad

    tried = ", ".join(s or "default" for s in sockets)
    detail = "; ".join(errors) or "no sockets attempted"
    raise KiCadConnectionError(
        f"Failed to connect to KiCad. Tried: {tried}. {detail}. "
        "Turn on Preferences → Plugins → Enable KiCad API. "
        "The Python interpreter in that dialog is for plugins, not this server."
    )


@lru_cache(maxsize=1)
def get_kicad():
    return _create_kicad()


def reset_connection() -> None:
    client = None
    try:
        if get_kicad.cache_info().hits + get_kicad.cache_info().misses > 0:
            try:
                client = get_kicad()
            except Exception:
                client = None
    except Exception:
        pass

    get_kicad.cache_clear()

    if client is not None:
        try:
            client.close()
            logger.info("Previous KiCad connection closed.")
        except Exception as e:
            logger.warning("Error while closing previous connection: %s", e)


def close_connection() -> None:
    reset_connection()


def get_board_or_raise():
    kicad = get_kicad()
    board = kicad.get_board()
    if board is None:
        raise KiCadConnectionError(
            "No board is currently open in KiCad. Open a .kicad_pcb first."
        )
    return board
