"""
Execution Result Module

Defines the ExecutionResult dataclass for trade execution outcomes.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionResult:
    """
    Result of a trade execution operation.

    Attributes:
        status: The outcome status (e.g., "PAPER", "SKIP", "BLOCKED", "ERROR")
        details: Human-readable description of the result
        telemetry: Optional dictionary with execution metrics and trade details
    """
    status: str
    details: str
    telemetry: Optional[dict] = None
