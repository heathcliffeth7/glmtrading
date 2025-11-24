import argparse
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from app.config.settings import get_settings


settings = get_settings()


def fetch_futures_metrics(symbol: str, days: int) -> pd.DataFrame:
    base_url = str(settings.binance.futures_rest_endpoint)
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    params = {"symbol": symbol.upper(), "period": "5m", "limit": 1000}
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        ratio = client.get("/futures/data/globalLongShortAccountRatio", params=params).json()
        interest = client.get("/futures/data/openInterestHist", params=params).json()
        funding = client.get("/fapi/v1/fundingRate", params={"symbol": symbol.upper(), "limit": 1000}).json()
    df_ratio = pd.DataFrame(ratio)[["timestamp", "longShortRatio"]]
    df_interest = pd.DataFrame(interest)[["timestamp", "sumOpenInterestValue"]]
    df_funding = pd.DataFrame(funding)[["fundingTime", "fundingRate"]]
    df_ratio["timestamp"] = pd.to_datetime(df_ratio["timestamp"], unit="ms")
    df_interest["timestamp"] = pd.to_datetime(df_interest["timestamp"], unit="ms")
    df_funding["timestamp"] = pd.to_datetime(df_funding["fundingTime"], unit="ms")
    df = df_ratio.merge(df_interest, on="timestamp", how="outer").merge(df_funding, on="timestamp", how="outer")
    df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)].sort_values("timestamp")
    df.rename(
        columns={
            "longShortRatio": "long_short_ratio",
            "sumOpenInterestValue": "open_interest",
            "fundingRate": "funding_rate",
        },
        inplace=True,
    )
    return df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("days", type=int, default=30)
    parser.add_argument("--output", default="data/binance_futures_metrics.csv")
    args = parser.parse_args()

    df = fetch_futures_metrics(args.symbol, args.days)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)
    print(f"Exported {len(df)} rows to {output}")


if __name__ == "__main__":
    main()
