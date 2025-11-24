import argparse
import json
from datetime import datetime
from pathlib import Path

from app.agents.derivatives import DerivativesAgent
from app.agents.long_term import LongTermAgent
from app.agents.short_term import ShortTermAgent
from app.research.backtest import Backtester, load_historical_from_influx


AGENT_MAP = {
    "short_term": ShortTermAgent,
    "long_term": LongTermAgent,
    "derivatives": DerivativesAgent,
}


def run_backtest(
    symbol: str,
    interval: str,
    minutes: int,
    agent_name: str,
    start: str | None = None,
    end: str | None = None,
    output: Path | None = None,
) -> None:
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    data = load_historical_from_influx(symbol, interval, minutes, start=start_dt, end=end_dt)
    if data.empty:
        print("No data available for backtest")
        return
    agent_cls = AGENT_MAP.get(agent_name, ShortTermAgent)
    agent = agent_cls()
    tester = Backtester(agent)
    trades = tester.run(data.to_dict(orient="records"))
    summary = tester.summary()
    metrics = tester.metrics()
    print(f"Backtest completed with {len(trades)} trades")
    print(f"Portfolio value: {summary['portfolio_value']:.2f}")
    print(f"Sharpe: {metrics['sharpe']:.4f} | Max Drawdown: {metrics['max_drawdown']:.4f} | Return: {metrics['return']:.2%}")
    if output:
        payload = {
            "summary": summary,
            "metrics": metrics,
            "trades": [trade.__dict__ for trade in trades],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, default=str, indent=2))
        print(f"Results written to {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("interval")
    parser.add_argument("minutes", type=int, default=1440)
    parser.add_argument("--agent", choices=AGENT_MAP.keys(), default="short_term")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--output")
    args = parser.parse_args()

    run_backtest(
        args.symbol,
        args.interval,
        args.minutes,
        agent_name=args.agent,
        start=args.start,
        end=args.end,
        output=Path(args.output) if args.output else None,
    )


if __name__ == "__main__":
    main()
