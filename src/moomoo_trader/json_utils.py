from typing import Any

import pandas as pd


def sanitize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(item) for item in value]

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    if isinstance(value, (str, bool, int, float)):
        return value

    if hasattr(value, "item"):
        try:
            return sanitize_for_json(value.item())
        except (TypeError, ValueError):
            pass

    if hasattr(value, "tolist") and not isinstance(value, (str, bytes, bytearray)):
        try:
            return sanitize_for_json(value.tolist())
        except TypeError:
            pass

    return str(value)


def stringify_identifier(value: Any) -> Any:
    normalized = sanitize_for_json(value)
    if normalized is None:
        return None
    return str(normalized)


def frame_to_records(frame) -> list:
    if frame is None:
        return []

    if hasattr(frame, "to_dict"):
        return frame.to_dict(orient="records")

    if isinstance(frame, list):
        return frame

    return [frame]
