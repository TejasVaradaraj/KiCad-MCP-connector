from __future__ import annotations

from typing import Any, Optional

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from kicad_mcp.connection import get_kicad

class TitleBlock(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    revision: Optional[str] = None
    company: Optional[str] = None
    comments: dict[int, str] = Field(default_factory=dict)
    message: str = ""

class LibraryId(BaseModel):
    library: str
    name: str
    full: str
    message: str = ""

class TextInfo(BaseModel):
    value: str
    position_mm: Optional[dict[str, float]] = None
    angle_deg: Optional[float] = None
    visible: Optional[bool] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    raw: dict[str, Any] = Field(default_factory=dict)

class SimpleResult(BaseModel):
    success: bool
    message: str
    data: Optional[dict[str, Any]] = None

def _board():
    kicad = get_kicad()
    board = kicad.get_board()
    if board is None:
        raise RuntimeError("No board is currently open in KiCad.")
    return board

def _title_block_to_model(tb) -> TitleBlock:
    comments = {}
    try:
        raw_comments = getattr(tb, "comments", None) or {}
        comments = {int(k): str(v) for k, v in dict(raw_comments).items()}
    except Exception:
        pass

    return TitleBlock(
        title=getattr(tb, "title", None),
        date=getattr(tb, "date", None),
        revision=getattr(tb, "revision", None),
        company=getattr(tb, "company", None),
        comments=comments,
        message="Title block retrieved.",
    )

def register_common_types_tools(mcp: MCPServer) -> None:

    @mcp.tool()
    def board_get_title_block() -> TitleBlock:
        """
        Get the title block information of the currently open board
        (title, date, revision, company, comments).
        """
        try:
            board = _board()
            tb = board.get_title_block_info()
            return _title_block_to_model(tb)
        except Exception as e:
            return TitleBlock(message=f"Failed to read title block: {e}")

    @mcp.tool()
    def board_set_title_block(
        title: Optional[str] = None,
        date: Optional[str] = None,
        revision: Optional[str] = None,
        company: Optional[str] = None,
        comments: Optional[dict[int, str]] = None,
    ) -> SimpleResult:
        """
        Update fields of the board title block.

        Only the fields you pass are changed; omitted fields are left as-is.
        """
        try:
            from kipy.common_types import TitleBlockInfo

            board = _board()
            tb = board.get_title_block_info()

            if title is not None:
                tb.title = title
            if date is not None:
                tb.date = date
            if revision is not None:
                tb.revision = revision
            if company is not None:
                tb.company = company
            if comments is not None:
                # Best-effort; exact mutation API can vary slightly by version
                try:
                    tb.comments = comments
                except Exception:
                    pass

            commit = board.begin_commit()
            try:
                board.set_title_block_info(tb)
                board.push_commit(commit, "Update title block")
                return SimpleResult(success=True, message="Title block updated.")
            except Exception as e:
                board.drop_commit(commit)
                return SimpleResult(success=False, message=f"Failed to write title block: {e}")
        except Exception as e:
            return SimpleResult(success=False, message=str(e))

    @mcp.tool()
    def parse_library_id(full_id: str) -> LibraryId:
        """
        Parse a KiCad library identifier string of the form 'Library:Name'
        (e.g. "Device:R", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical").

        Returns the library nickname and entry name separately.
        """
        if ":" not in full_id:
            return LibraryId(
                library="",
                name=full_id,
                full=full_id,
                message="No library prefix found; treating entire string as entry name.",
            )
        library, name = full_id.split(":", 1)
        return LibraryId(
            library=library,
            name=name,
            full=full_id,
            message=f"Parsed as library='{library}', name='{name}'.",
        )

    @mcp.tool()
    def format_library_id(library: str, name: str) -> LibraryId:
        """
        Build a canonical Library:Name identifier from separate parts.
        """
        full = f"{library}:{name}"
        return LibraryId(library=library, name=name, full=full, message=f"Formatted as '{full}'.")

    @mcp.tool()
    def board_list_text(limit: int = 40) -> list[dict[str, Any]]:
        """
        List text objects (BoardText / BoardTextBox) currently on the board.

        Returns a compact summary (value + position) so the agent can see
        labels, notes, and other text without dumping full attribute trees.
        """
        from kicad_mcp.helpers.units import nm_to_mm

        board = _board()
        texts = list(board.get_text())[:limit]
        result = []
        for t in texts:
            entry: dict[str, Any] = {
                "type": type(t).__name__,
                "value": getattr(t, "value", None),
            }
            pos = getattr(t, "position", None)
            if pos is not None:
                entry["position_mm"] = {
                    "x": nm_to_mm(getattr(pos, "x", 0)),
                    "y": nm_to_mm(getattr(pos, "y", 0)),
                }
            # TextBox uses top_left / bottom_right instead of position sometimes
            if "position_mm" not in entry:
                tl = getattr(t, "top_left", None)
                if tl is not None:
                    entry["top_left_mm"] = {
                        "x": nm_to_mm(getattr(tl, "x", 0)),
                        "y": nm_to_mm(getattr(tl, "y", 0)),
                    }
            result.append(entry)
        return result

    @mcp.tool()
    def expand_text_variables(text: str) -> SimpleResult:
        """
        Expand KiCad text variables in a string using the current board context
        (e.g. ${TITLE}, ${REVISION}, project variables, etc.).

        Variables that do not exist are left unchanged.
        """
        try:
            board = _board()
            expanded = board.expand_text_variables(text)
            return SimpleResult(
                success=True,
                message="Variables expanded.",
                data={"original": text, "expanded": expanded},
            )
        except Exception as e:
            return SimpleResult(success=False, message=str(e))
