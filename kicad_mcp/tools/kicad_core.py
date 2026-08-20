from __future__ import annotations

from typing import Any, Optional

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from kicad_mcp.connection import get_kicad
from kicad_mcp.helpers.documents import (
    document_path,
    pcb_and_schematic_types,
    specifier_to_dict,
)

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
    raw: dict[str, Any] = Field(default_factory=dict)
    message: str = ""

class BoardSummary(BaseModel):
    available: bool
    message: str

class SchematicSummary(BaseModel):
    available: bool
    message: str

def register_kicad_core_tools(mcp: MCPServer) -> None:

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

        if doc_type is not None and doc_type != 0:
            types_to_try = [doc_type]
        else:
            types_to_try = pcb_and_schematic_types()

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
                raw = specifier_to_dict(doc, dtype)
                result.append(
                    DocumentInfo(
                        path=document_path(doc),
                        type=str(raw.get("type_name") or raw.get("type") or dtype),
                        raw=raw,
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

        KiCad 9/10 GUI sessions do not support this. Headless open via
        kicad-cli api-server is KiCad 11+. Open the file in the KiCad GUI instead.
        """
        kicad = get_kicad()
        if not hasattr(kicad, "open_document"):
            return DocumentInfo(
                path=path,
                type=str(doc_type),
                raw={"unsupported": True, "path": path, "type": doc_type},
                message=(
                    "Opening documents via IPC requires KiCad 11 headless mode "
                    "(kicad-cli api-server). This KiCad 10 / kicad-python session "
                    "does not implement open_document. Open the file in the GUI."
                ),
            )
        doc = kicad.open_document(path, doc_type)
        return DocumentInfo(
            path=path,
            type=str(doc_type),
            raw={"path": path, "type": doc_type, "specifier": str(doc)},
            message="Document opened.",
        )

    @mcp.tool()
    def kicad_close_document(document_raw: dict[str, Any]) -> str:
        """
        Close an open document (headless / KiCad 11+ only).

        Not supported against a normal KiCad 10 GUI instance.
        """
        kicad = get_kicad()
        if not hasattr(kicad, "close_document"):
            return (
                "Closing documents via IPC requires KiCad 11 headless mode. "
                "This KiCad 10 / kicad-python session does not implement "
                "close_document. Close the file in the GUI."
            )
        raise NotImplementedError(
            "Reconstructing DocumentSpecifier from serialized form is not "
            "implemented. Close the file in the KiCad GUI."
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
            Reverse-DNS plugin identifier, e.g. "org.kicad.kicad-mcp".
            A bare string like "test" is rejected by KiCad.
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
