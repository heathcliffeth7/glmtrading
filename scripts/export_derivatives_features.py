import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.utils.influx import query_range_between


def export(symbol: str, interval: str, days: int, output: Path) -> None:
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    measurement = f"features_{interval}"
    records = query_range_between(measurement, symbol, interval, start, end)
    if not records:
        print("No records found")
        return
    df = pd.DataFrame(records)
    pivoted = df.pivot(index="timestamp", columns="field", values="value").reset_index()
    output.parent.mkdir(parents=True, exist_ok=True)
    pivoted.to_csv(output, index=False)
    print(f"Exported {len(pivoted)} rows to {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("interval")
    parser.add_argument("days", type=int, default=30)
    parser.add_argument("--output", default="data/derivatives_features.csv")
    args = parser.parse_args()

    export(args.symbol, args.interval, args.days, Path(args.output))


if __name__ == "__main__":
    main()
