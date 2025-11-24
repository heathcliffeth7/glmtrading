#!/usr/bin/env python3
import sys
import json
from pathlib import Path

from app.risk_manager.manager import RiskManager


def read_input() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.read()
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        return p.read_text(encoding="utf-8")
    print("Usage: evaluate_text_payload.py <payload.txt> or pipe text via stdin", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    payload = read_input()
    rm = RiskManager()
    decision = rm.evaluate_text_payload(payload)
    out = {
        "action": decision.action,
        "amount": decision.amount,
        "leverage": decision.leverage,
        "reasoning": decision.reasoning,
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
