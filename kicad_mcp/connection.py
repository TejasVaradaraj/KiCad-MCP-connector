# kicad_mcp/connection.py
"""
KiCad IPC connection manager.

Owns the single shared `kipy.KiCad` client used by all tools.
Connection is created lazily on first use and cached for the lifetime
of the process (typical for stdio MCP servers).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

logger = logging.getLogger("kicad-mcp.connection")

# ---------------------------------------------------------------------------
# Configuration (override via environment variables if desired)
# ---------------------------------------------------------------------------

# Optional explicit socket path. If unset, kicad-python uses
# KICAD_API_SOCKET or the platform default.
KICAD_SOCKET_PATH: Optional[str] = os.environ.get("KICAD_MCP_SOCKET")

# Optional token. If unset, kicad-python uses KICAD_API_TOKEN.
KICAD_TOKEN: Optional[str] = os.environ.get("KICAD_MCP_TOKEN")

# Client name shown in KiCad / logs.
CLIENT_NAME: str = os.environ.get("KICAD_MCP_CLIENT_NAME", "kicad-mcp")

# Request timeout in milliseconds.
TIMEOUT_MS: int = int(os.environ.get("KICAD_MCP_TIMEOUT_MS", "5000"))

# Headless mode (kicad-cli api-server). Default is normal GUI connection.
HEADLESS: bool = os.environ.get("KICAD_MCP_HEADLESS", "").lower() in ("1", "true", "yes")

# Optional path to kicad-cli binary (only needed for headless in some setups).
KICAD_CLI_PATH: Optional[str] = os.environ.get("KICAD_MCP_CLI_PATH")

# Optional file to pre-load when starting headless.
HEADLESS_FILE_PATH: Optional[str] = os.environ.get("KICAD_MCP_FILE")


class KiCadConnectionError(RuntimeError):
    """Raised when we cannot establish or use a KiCad connection."""

def _create_kicad():
    """Create a new KiCad client instance from current config."""
    try:
        from kipy import KiCad
    except ImportError as e:
        raise KiCadConnectionError(
            "kicad-python is not installed. Run: uv add kicad-python"
        ) from e

    import inspect

    wanted = {
        "client_name": CLIENT_NAME,
        "timeout_ms": TIMEOUT_MS,
        "headless": HEADLESS,
        "socket_path": KICAD_SOCKET_PATH,
        "kicad_token": KICAD_TOKEN,
        "kicad_cli_path": KICAD_CLI_PATH,
        "file_path": HEADLESS_FILE_PATH if HEADLESS else None,
    }

    params = inspect.signature(KiCad.__init__).parameters
    kwargs = {
        key: value
        for key, value in wanted.items()
        if value is not None and key in params
    }

    logger.info("Connecting to KiCad with kwargs=%s", list(kwargs))

    try:
        kicad = KiCad(**kwargs)
    except Exception as e:
        raise KiCadConnectionError(
            f"Failed to connect to KiCad: {e}. "
            "Ensure KiCad is running with the API enabled "
            "(Preferences → Plugins → Enable API server)."
        ) from e

    try:
        kicad.ping()
    except Exception as e:
        try:
            kicad.close()
        except Exception:
            pass
        raise KiCadConnectionError(
            f"Connected but ping failed: {e}."
        ) from e

    logger.info("KiCad connection established.")
    return kicad


@lru_cache(maxsize=1)
def get_kicad():
    """
    Return the shared KiCad client (lazy singleton).

    First call creates the connection; subsequent calls reuse it.
    Raises KiCadConnectionError on failure.
    """
    return _create_kicad()


def reset_connection() -> None:
    """
    Drop the cached connection so the next get_kicad() creates a new one.

    Useful after KiCad restarts or when switching between GUI / headless.
    """
    client = None
    try:
        # Peek at the cache without creating a new connection.
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
    """Explicitly close and clear the connection (e.g. on server shutdown)."""
    reset_connection()


def get_board_or_raise():
    """
    Convenience helper used by many tools:
    return the open board or raise a clear error.
    """
    kicad = get_kicad()
    board = kicad.get_board()
    if board is None:
        raise KiCadConnectionError(
            "No board is currently open in KiCad. "
            "Open a .kicad_pcb file first."
        )
    return board