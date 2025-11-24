#!/usr/bin/env python3
from __future__ import annotations

from sqlalchemy.orm import Session

try:
    # Reuse app engine/models
    from app.executor.ledger import engine, Trade
except Exception as exc:  # noqa: BLE001
    print(f"Failed to import DB models: {exc}")
    raise SystemExit(1)


def main() -> None:
    MAX_BTC = 2.0
    MAX_NOTIONAL = 100_000.0  # display/audit threshold (unleveraged)

    with Session(engine) as session:
        anomalies: list[Trade] = (
            session.query(Trade)
            .filter(
                (Trade.amount > MAX_BTC)
                | (Trade.notional_value != None)  # noqa: E711
            )
            .order_by(Trade.timestamp.desc())
            .all()
        )

        flagged = []
        for t in anomalies:
            notional = abs((t.notional_value or 0.0))
            if t.amount > MAX_BTC or notional > MAX_NOTIONAL:
                flagged.append((t.id, t.side, t.amount, t.price, t.leverage or 1.0, notional, t.timestamp))

        if not flagged:
            print("No anomalies detected.")
            return

        print("Anomalous trades detected (amount>2 BTC or notional>$100k):")
        for row in flagged:
            tid, side, amt, price, lev, notional, ts = row
            print(f"  id={tid} {side} {amt:.6f} BTC @ ${price:,.2f} lev={lev:.1f}x notional=${notional:,.2f} ts={ts}")


if __name__ == "__main__":
    main()
