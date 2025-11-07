"""Runtime tracking for nof1.ai style GLM prompts."""

from datetime import datetime
from threading import Lock
from typing import Dict


class RuntimeTracker:
    """Singleton runtime tracker for trading system."""

    _instance = None
    _lock = Lock()
    _start_time: datetime | None = None
    _invocation_count: int = 0

    def __init__(self) -> None:
        if RuntimeTracker._start_time is None:
            RuntimeTracker._start_time = datetime.utcnow()
            RuntimeTracker._invocation_count = 0

    @classmethod
    def get_instance(cls) -> "RuntimeTracker":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def increment(self) -> None:
        """Increment invocation count."""
        with self._lock:
            RuntimeTracker._invocation_count += 1

    def get_runtime_info(self) -> Dict[str, float | int | str]:
        """Get runtime information for prompt."""
        if RuntimeTracker._start_time is None:
            RuntimeTracker._start_time = datetime.utcnow()

        now = datetime.utcnow()
        minutes_since_start = (now - RuntimeTracker._start_time).total_seconds() / 60

        return {
            "minutes_since_start": minutes_since_start,
            "invocation_count": RuntimeTracker._invocation_count,
            "current_time": now.isoformat(),
        }

    def reset(self) -> None:
        """Reset runtime tracker (for testing)."""
        with self._lock:
            RuntimeTracker._start_time = datetime.utcnow()
            RuntimeTracker._invocation_count = 0
