import pytest
from sqlalchemy.orm import Session

from app.executor.executor import Executor
from app.executor.ledger import DailyPnL, Portfolio, Trade, engine, record_trade


def _reset_tables(session: Session) -> None:
    session.query(Trade).delete()
    session.query(DailyPnL).delete()
    session.query(Portfolio).delete()
    session.commit()


def test_recent_trades_skip_anomalies_and_reconstruct_close_price() -> None:
    executor = Executor()
    executor._resolve_price = lambda: 101_000.0  # deterministic snapshot price

    with Session(engine) as session:
        _reset_tables(session)

        record_trade(
            session,
            symbol=executor._symbol,
            side="BUY",
            amount=0.1,
            price=100_000.0,
            pnl=100.0,
            leverage=5.0,
            fees=50.0,
            close_price=100_000.0,  # intentionally equal to entry to trigger reconstruction
            position_side="LONG",
        )
        session.flush()

        record_trade(
            session,
            symbol=executor._symbol,
            side="BUY",
            amount=5.0,  # exceeds guardrail (2 × max_btc_per_trade)
            price=100_000.0,
            pnl=0.0,
            leverage=2.0,
            fees=0.0,
            close_price=None,
            position_side="LONG",
        )
        session.commit()

    recent = executor.get_recent_trades_with_pnl(limit=5)

    assert executor._last_trade_report_stats["skipped_anomalies"] == 1
    assert len(recent) == 1

    entry = recent[0]
    expected_close = 100_000.0 + (100.0 + 50.0) / 0.1
    assert entry["close_price"] == pytest.approx(expected_close)
    assert entry["open_price"] == 100_000.0
    assert entry["pnl"] == pytest.approx(100.0)
    assert entry["fee"] == pytest.approx(50.0)
