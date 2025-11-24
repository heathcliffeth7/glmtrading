import argparse
from pathlib import Path

import pandas as pd

from app.agents.workflows.derivatives_workflow import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("features_csv")
    parser.add_argument("futures_csv")
    parser.add_argument("--output", default="models/derivatives.joblib")
    args = parser.parse_args()

    features = pd.read_csv(args.features_csv)
    futures = pd.read_csv(args.futures_csv)
    df = features.merge(futures, on="timestamp", how="inner")
    if "future_move" not in df.columns:
        df["future_move"] = df["close"].shift(-1).fillna(df["close"]).gt(df["close"]).astype(int)
    path, metadata = run(df)
    target = Path(args.output)
    if target != path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(path).read_bytes())
    print(f"Model saved to {target} with metadata {metadata}")


if __name__ == "__main__":
    main()
