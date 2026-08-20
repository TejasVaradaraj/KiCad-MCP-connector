from __future__ import annotations

F_CU = 3
B_CU = 34


def copper_layer_ids() -> tuple[int, int]:
    try:
        from kipy.proto.board.board_types_pb2 import BoardLayer

        return int(BoardLayer.BL_F_Cu), int(BoardLayer.BL_B_Cu)
    except Exception:
        return F_CU, B_CU


def nonzero_layer_ids(layers) -> list[int]:
    result: list[int] = []
    for layer in layers or []:
        try:
            value = int(layer)
        except (TypeError, ValueError):
            continue
        if value:
            result.append(value)
    return result
