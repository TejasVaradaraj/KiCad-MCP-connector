# kicad_mcp/tools/kicad_core.py
"""
MCP tools derived from kipy.KiCad (the top-level connection class).

These tools manage connection health, document discovery, and basic
headless document control. Board/Schematic-specific operations come later.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from mcp.server import MCPServer
from pydantic import BaseModel, Field

# Assume you have a shared connection manager (shown at the bottom)
from kicad_mcp.connection import get_kicad


# ---------------------------------------------------------------------------
# Shared response models (clean, serialisable)
# ---------------------------------------------------------------------------

class KiCadStatus(BaseModel):
    connected: bool
    ping_ok: bool
    version: Optional[str] = None
    api_version: Optional[str] = None
    version_match: Optional[bool] = None
    message: str


class DocumentInfo(BaseModel):
    path: Optional[str] = None
    type: Optional[str] = None
    # Add more fields later once we inspect DocumentSpecifier fully
    raw: dict[str, Any] = Field(default_factory=dict)


class BoardSummary(BaseModel):
    available: bool
    message: str
    # We will expand this significantly in the Board section


class SchematicSummary(BaseModel):
    available: bool
    message: str


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def register_kicad_core_tools(mcp: MCPServer) -> None:
    """Register all tools that come from the top-level KiCad class."""

    @mcp.tool()
    def kicad_get_status() -> KiCadStatus:
        """
        Check connection health and version information of the running KiCad instance.

        Use this first when starting a session or when you suspect the connection
        may have been lost. Returns whether KiCad is reachable, the running version,
        the API version the library was built against, and whether they match.
        """
        kicad = get_kicad()
        kicad.ping()
        version = str(kicad.get_version())
        api_version = str(kicad.get_api_version())
        try:
            match = bool(kicad.check_version())
            extra = ""
        except Exception as e:
            match = False
            extra = f" Version check warning: {e}"

        return KiCadStatus(
            connected=True,
            ping_ok=True,
            version=version,
            api_version=api_version,
            version_match=match,
            message="KiCad is reachable." + extra,
        )

    @mcp.tool()
    def kicad_list_open_documents(
        doc_type: Optional[int] = None,
    ) -> list[DocumentInfo]:
        """
        List documents currently open in KiCad.

        Parameters
        ----------
        doc_type:
            Optional document type integer. If omitted, the tool queries
            common types (PCB, then schematic) instead of type 0, which
            KiCad rejects as unhandled.
        """
        kicad = get_kicad()

        types_to_try: list[int] = []
        if doc_type is not None and doc_type != 0:
            types_to_try = [doc_type]
        else:
            # Discover official enum values when available; fall back to PCB=2, SCH=1
            try:
                from kipy.proto.common.types.base_types_pb2 import DocumentType
                for name in ("DOCTYPE_PCB", "DOCTYPE_SCHEMATIC", "DOCTYPE_PROJECT"):
                    if hasattr(DocumentType, name):
                        types_to_try.append(int(getattr(DocumentType, name)))
            except Exception:
                types_to_try = [2, 1]  # typical PCB, schematic

            if not types_to_try:
                types_to_try = [2, 1]

        result: list[DocumentInfo] = []
        errors: list[str] = []
        seen: set[str] = set()

        for dtype in types_to_try:
            try:
                docs = kicad.get_open_documents(dtype)
            except Exception as e:
                errors.append(f"type {dtype}: {e}")
                continue
            for doc in docs:
                key = str(doc)
                if key in seen:
                    continue
                seen.add(key)
                result.append(
                    DocumentInfo(
                        raw={"type": dtype, "specifier": key},
                    )
                )

        if not result and errors:
            raise RuntimeError(
                "get_open_documents is not usable in this KiCad session: "
                + "; ".join(errors)
            )
        return result

    @mcp.tool()
    def kicad_get_board() -> BoardSummary:
        """
        Get a reference to the currently open PCB (board) in KiCad, if any.

        Returns a summary indicating whether a board is open. Full board
        inspection and mutation tools will be added in the Board section.
        """
        try:
            kicad = get_kicad()
            board = kicad.get_board()
            if board is None:
                return BoardSummary(
                    available=False,
                    message="No board is currently open in KiCad.",
                )
            return BoardSummary(
                available=True,
                message="Board is open and accessible. Use Board-specific tools for details.",
            )
        except Exception as e:
            return BoardSummary(
                available=False,
                message=f"Error retrieving board: {e}",
            )

    @mcp.tool()
    def kicad_get_schematic() -> SchematicSummary:
        """
        Get the currently open schematic in KiCad (requires newer KiCad / kicad-python).

        Note: Official schematic IPC support is still maturing. This tool will
        succeed only on versions that implement get_schematic().
        """
        try:
            kicad = get_kicad()
            sch = kicad.get_schematic()
            if sch is None:
                return SchematicSummary(
                    available=False,
                    message="No schematic is currently open (or API not available).",
                )
            return SchematicSummary(
                available=True,
                message="Schematic is open and accessible.",
            )
        except Exception as e:
            return SchematicSummary(
                available=False,
                message=f"Schematic API not available or error: {e}",
            )

    @mcp.tool()
    def kicad_open_document(path: str, doc_type: int) -> DocumentInfo:
        """
        Open a document in headless mode.

        Only supported when connected to a headless kicad-cli api-server.
        Not supported against a normal GUI KiCad instance.

        Parameters
        ----------
        path:
            Absolute path to the board, schematic, or project file.
        doc_type:
            Document type integer (board / schematic / etc.).
        """
        kicad = get_kicad()
        doc = kicad.open_document(path, doc_type)
        return DocumentInfo(raw={"path": path, "type": doc_type, "specifier": str(doc)})

    @mcp.tool()
    def kicad_close_document(document_raw: dict[str, Any]) -> str:
        """
        Close an open document (headless mode only).

        Pass the document information previously returned by
        kicad_list_open_documents or kicad_open_document.
        """
        kicad = get_kicad()
        # In real code you would reconstruct a proper DocumentSpecifier here.
        # For now we keep the interface honest about the current limitation.
        raise NotImplementedError(
            "Reconstructing DocumentSpecifier from serialized form needs the "
            "exact protobuf fields. Will be completed once Common Types are reviewed."
        )

    @mcp.tool()
    def kicad_get_plugin_settings_path(identifier: str) -> str:
        """
        Return a writable directory that a plugin can use for persistent settings.

        The directory may not exist yet – create it if needed.
        Files placed here survive plugin upgrades/uninstalls.

        Parameters
        ----------
        identifier:
            Full plugin identifier, e.g. "org.kicad.my-mcp-server"
        """
        kicad = get_kicad()
        return kicad.get_plugin_settings_path(identifier)

    @mcp.tool()
    def kicad_get_binary_path(binary_name: str = "kicad-cli") -> str:
        """
        Return the full path to a KiCad binary (kicad-cli, etc.).

        Useful when you need to invoke kicad-cli for export / DRC / other
        operations that are still better done through the CLI.
        """
        kicad = get_kicad()
        return kicad.get_kicad_binary_path(binary_name)


# ---------------------------------------------------------------------------
# Minimal connection manager (put in kicad_mcp/connection.py)
# ---------------------------------------------------------------------------

"""
# kicad_mcp/connection.py
from __future__ import annotations
from functools import lru_cache
from kipy import KiCad

@lru_cache(maxsize=1)
def get_kicad() -> KiCad:
    # You can later make this configurable (socket, headless, etc.)
    return KiCad()
"""