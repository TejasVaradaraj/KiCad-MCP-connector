from __future__ import annotations

from typing import Any, Optional

DOCTYPE_SCHEMATIC = 1
DOCTYPE_PCB = 3


def pcb_and_schematic_types() -> list[int]:
    try:
        from kipy.proto.common.types.base_types_pb2 import DocumentType

        types: list[int] = []
        for name in ("DOCTYPE_PCB", "DOCTYPE_SCHEMATIC"):
            if hasattr(DocumentType, name):
                types.append(int(getattr(DocumentType, name)))
        if types:
            return types
    except Exception:
        pass
    return [DOCTYPE_PCB, DOCTYPE_SCHEMATIC]


def specifier_to_dict(doc: Any, dtype: Optional[int] = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        data["type"] = int(getattr(doc, "type", dtype) or 0) or dtype
    except Exception:
        data["type"] = dtype
    try:
        from kipy.proto.common.types.base_types_pb2 import DocumentType

        type_id = data.get("type")
        if type_id is not None:
            data["type_name"] = DocumentType.Name(int(type_id))
    except Exception:
        pass
    for attr in ("board_filename", "filename"):
        try:
            value = getattr(doc, attr, None)
            if value:
                data["filename"] = str(value)
                break
        except Exception:
            continue
    try:
        project = getattr(doc, "project", None)
        if project is not None:
            data["project_name"] = getattr(project, "name", None) or None
            data["project_path"] = getattr(project, "path", None) or None
    except Exception:
        pass
    if "filename" not in data:
        data["specifier"] = str(doc)
    return data


def document_path(doc: Any) -> Optional[str]:
    info = specifier_to_dict(doc)
    filename = info.get("filename")
    project_path = info.get("project_path")
    if filename and project_path:
        from pathlib import Path

        return str(Path(str(project_path)) / str(filename))
    if filename:
        return str(filename)
    return None
