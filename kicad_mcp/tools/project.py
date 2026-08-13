# kicad_mcp/tools/project.py
"""
MCP tools derived from kipy.project.Project.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from kicad_mcp.connection import get_kicad


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ProjectInfo(BaseModel):
    available: bool
    name: Optional[str] = None
    path: Optional[str] = None
    message: str = ""


class NetClassInfo(BaseModel):
    name: str
    raw: dict[str, Any] = Field(default_factory=dict)


class TextVariablesInfo(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class SimpleResult(BaseModel):
    success: bool
    message: str
    data: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_project():
    """
    Obtain a Project object.

    Preference order:
    1. From the currently open board (most common case)
    2. From open documents via KiCad.get_project()
    """
    kicad = get_kicad()

    # 1. Prefer board → project
    try:
        board = kicad.get_board()
        if board is not None:
            proj = board.get_project()
            if proj is not None:
                return proj
    except Exception:
        pass

    # 2. Fall back to open documents
    try:
        # doc_type 0 is a common "any"/unspecified filter in practice;
        # adjust if your binding exposes a proper enum.
        docs = list(kicad.get_open_documents(0))
        if not docs:
            raise RuntimeError("No open documents found.")
        return kicad.get_project(docs[0])
    except Exception as e:
        raise RuntimeError(f"Could not obtain Project: {e}") from e


def _netclass_to_dict(nc) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for attr in (
        "name", "wire_width", "bus_width", "via_diameter", "via_drill",
        "diff_pair_width", "diff_pair_gap", "clearance", "track_width",
    ):
        if hasattr(nc, attr):
            try:
                val = getattr(nc, attr)
                data[attr] = val if isinstance(val, (str, int, float, bool, type(None))) else str(val)
            except Exception:
                pass
    if not data:
        data["repr"] = str(nc)[:300]
    # Ensure name key exists
    if "name" not in data:
        data["name"] = getattr(nc, "name", str(nc))
    return data


def _text_variables_to_dict(tv) -> dict[str, str]:
    """
    Best-effort conversion of TextVariables to a plain dict[str, str].
    The exact shape of TextVariables is lightly documented, so we try
    several common patterns.
    """
    result: dict[str, str] = {}

    # Pattern 1: mapping-like
    try:
        if hasattr(tv, "items"):
            for k, v in tv.items():
                result[str(k)] = str(v)
            if result:
                return result
    except Exception:
        pass

    # Pattern 2: .variables or .map attribute
    for attr in ("variables", "map", "values"):
        try:
            container = getattr(tv, attr, None)
            if container is None:
                continue
            if hasattr(container, "items"):
                for k, v in container.items():
                    result[str(k)] = str(v)
            elif isinstance(container, dict):
                result = {str(k): str(v) for k, v in container.items()}
            if result:
                return result
        except Exception:
            continue

    # Pattern 3: fallback
    result["_raw"] = str(tv)[:500]
    return result


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def register_project_tools(mcp: MCPServer) -> None:

    @mcp.tool()
    def project_get_info() -> ProjectInfo:
        """
        Get basic information about the current KiCad project
        (name and file path).
        """
        try:
            proj = _get_project()
            return ProjectInfo(
                available=True,
                name=getattr(proj, "name", None),
                path=getattr(proj, "path", None),
                message="Project is available.",
            )
        except Exception as e:
            return ProjectInfo(available=False, message=str(e))

    @mcp.tool()
    def project_get_net_classes() -> list[NetClassInfo]:
        """
        List all net classes defined in the project.

        Net classes control default clearances, track widths, via sizes, etc.
        """
        proj = _get_project()
        classes = list(proj.get_net_classes())
        return [
            NetClassInfo(
                name=str(getattr(nc, "name", f"class_{i}")),
                raw=_netclass_to_dict(nc),
            )
            for i, nc in enumerate(classes)
        ]

    @mcp.tool()
    def project_get_text_variables() -> TextVariablesInfo:
        """
        Retrieve all text variables defined at the project level.

        These are the ${VARIABLE} substitutions available in text objects,
        title blocks, etc.
        """
        try:
            proj = _get_project()
            tv = proj.get_text_variables()
            variables = _text_variables_to_dict(tv)
            return TextVariablesInfo(
                variables={k: v for k, v in variables.items() if k != "_raw"},
                raw=variables,
                message=f"Found {len(variables)} variable entries.",
            )
        except Exception as e:
            return TextVariablesInfo(message=f"Failed to read text variables: {e}")

    @mcp.tool()
    def project_set_text_variables(
        variables: dict[str, str],
        merge: bool = True,
    ) -> SimpleResult:
        """
        Set project-level text variables.

        Parameters
        ----------
        variables:
            Mapping of variable name → value (without ${}).
        merge:
            If True (default), merge with existing variables.
            If False, attempt to replace the whole set (exact behaviour
            depends on the merge_mode supported by your kicad-python version).
        """
        try:
            from kipy.common_types import TextVariables  # may live elsewhere in some versions
        except ImportError:
            TextVariables = None  # type: ignore

        try:
            proj = _get_project()

            # Build a TextVariables object. Exact construction varies by version;
            # we try the most common patterns.
            tv_obj = None
            if TextVariables is not None:
                try:
                    tv_obj = TextVariables()
                    # Try mapping-style population
                    if hasattr(tv_obj, "variables"):
                        tv_obj.variables = variables
                    elif hasattr(tv_obj, "update"):
                        tv_obj.update(variables)
                    else:
                        for k, v in variables.items():
                            try:
                                tv_obj[k] = v
                            except Exception:
                                pass
                except Exception:
                    tv_obj = variables  # last resort – some bindings accept a dict

            if tv_obj is None:
                tv_obj = variables

            merge_mode = 1 if merge else 0
            proj.set_text_variables(tv_obj, merge_mode=merge_mode)

            return SimpleResult(
                success=True,
                message=f"Set {len(variables)} text variable(s).",
                data={"variables": variables, "merge": merge},
            )
        except Exception as e:
            return SimpleResult(
                success=False,
                message=f"Failed to set text variables: {e}. "
                        "You may need to adjust TextVariables construction for your kicad-python version.",
            )

    @mcp.tool()
    def project_expand_text_variables(text: str) -> SimpleResult:
        """
        Expand text variables in a string using the **project** scope
        (as opposed to board-scoped expansion).

        Example: "${TITLE} rev ${REVISION}" → "My Board rev A".
        Unknown variables are left unchanged.
        """
        try:
            proj = _get_project()
            expanded = proj.expand_text_variables(text)
            return SimpleResult(
                success=True,
                message="Expanded using project variables.",
                data={"original": text, "expanded": expanded},
            )
        except Exception as e:
            return SimpleResult(success=False, message=str(e))