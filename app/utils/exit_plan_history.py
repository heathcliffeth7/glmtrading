import json
from typing import Any, Dict, List

from app.utils.logging import get_logger


logger = get_logger(__name__)


def normalize_exit_plan_history(raw: Any) -> Dict[str, List[Dict]]:
    """
    Normalize exit_plan_history to the canonical shape:
        {"updates": [ ... ]}

    Historical data may be stored as:
      - None
      - list of update records
      - dict missing "updates"
      - JSON-encoded string
    """
    if raw is None:
        return {"updates": []}

    if isinstance(raw, dict):
        updates = raw.get("updates")
        if updates is None:
            raw["updates"] = []
        elif not isinstance(updates, list):
            raw["updates"] = list(updates) if isinstance(updates, tuple) else [updates]
        return raw  # type: ignore[return-value]

    if isinstance(raw, list):
        return {"updates": raw}  # type: ignore[return-value]

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return normalize_exit_plan_history(parsed)
        except Exception:
            logger.warning("Unexpected exit_plan_history string, resetting: %r", raw)
            return {"updates": []}

    logger.warning("Unexpected exit_plan_history type %s, resetting", type(raw).__name__)
    return {"updates": []}

