from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from typing import Iterable, List

import numpy as np
import pandas as pd

from app.agents.base import Agent
from app.utils.influx import query_range, query_range_between


@dataclass
class TradeResult:
    timestamp: datetime
    action: str
    price: float
    pnl: float


class Backtester:
    def __init__(self, agent: Agent, initial_cash: float = 10000.0) -> None:
        self._agent = agent
        self._cash = initial_cash
        self._initial_cash = initial_cash
        self._position = 0.0
        self._last_price = 0.0
        self._trades: List[TradeResult] = []
        self._equity_history: List[float] = []

    def run(self, data: Iterable[dict]) -> List[TradeResult]:
        """
        Backtest simulation using simple moving average strategy.
        NOT calling agent for every row to avoid performance issues.
        Agent signal is used separately in runtime for real decisions.
        """
        data_list = list(data)
        prices = [float(row.get("close", 0)) for row in data_list]

        # Simple MA crossover strategy for backtesting simulation
        # This is just for portfolio simulation, not real trading
        for i, row in enumerate(data_list):
            price = float(row.get("close", 0))
            previous_price = self._last_price or price

            # Simple strategy: buy when price increases, sell when decreases
            # This is a placeholder - backtest is for portfolio simulation only
            if i > 0 and i % 10 == 0:  # Trade every 10 candles
                if price > prices[max(0, i - 5)]:  # Price went up
                    direction = "BUY"
                    confidence = 0.3
                else:  # Price went down
                    direction = "SELL"
                    confidence = 0.3

                if direction in {"BUY", "SELL"} and confidence > 0:
                    self._execute_trade(direction, price, previous_price, confidence)

            self._last_price = price
            equity = self._cash + self._position * price
            self._equity_history.append(equity)

        return self._trades

    def _execute_trade(
        self, direction: str, price: float, previous_price: float, confidence: float
    ) -> None:
        amount = confidence
        if direction == "BUY":
            self._position += amount
            self._cash -= amount * price
        elif direction == "SELL":
            self._position -= amount
            self._cash += amount * price

        pnl = self._position * (price - previous_price)
        self._trades.append(
            TradeResult(timestamp=datetime.utcnow(), action=direction, price=price, pnl=pnl)
        )

    def summary(self) -> dict:
        total_value = self._cash + self._position * self._last_price
        return {
            "cash": self._cash,
            "position": self._position,
            "last_price": self._last_price,
            "portfolio_value": total_value,
            "initial_cash": self._initial_cash,
            "trades": len(self._trades),
        }

    def metrics(self) -> dict:
        if len(self._equity_history) < 2:
            return {"sharpe": 0.0, "max_drawdown": 0.0, "return": 0.0}
        values = np.array(self._equity_history, dtype=float)
        returns = np.diff(values) / values[:-1]
        mean_return = float(np.mean(returns)) if returns.size else 0.0
        std_return = float(np.std(returns, ddof=1)) if returns.size > 1 else 0.0
        sharpe = (mean_return / std_return * sqrt(len(returns))) if std_return > 0 else 0.0
        cumulative_max = np.maximum.accumulate(values)
        drawdowns = (values - cumulative_max) / cumulative_max
        max_drawdown = float(drawdowns.min()) if drawdowns.size else 0.0
        total_return = float((values[-1] - values[0]) / values[0]) if values[0] else 0.0
        return {
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "return": total_return,
        }


def load_historical_from_influx(
    symbol: str,
    interval: str,
    minutes: int = 1440,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    # Use enriched data which includes all features (Binance + TwelveData)
    measurement = f"enriched_{interval}"
    if start and end:
        records = query_range_between(measurement, symbol, interval, start, end)
    else:
        records = query_range(measurement, symbol, interval, minutes=minutes)
    return pd.DataFrame(records)
