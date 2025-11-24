"""QLib türev ajanı eğitim workflow'u."""

import json
from pathlib import Path
from typing import Tuple

import joblib
import pandas as pd


def run(data: pd.DataFrame, experiment: str = "derivatives") -> Tuple[Path, dict]:
    model = joblib.load("models/derivatives.joblib") if Path("models/derivatives.joblib").exists() else None
    if model is None:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression()
        X = data[["long_short_ratio", "open_interest", "funding_rate"]]
        y = (data["future_move"] > 0).astype(int)
        model.fit(X, y)

    models_dir = Path("models")
    models_dir.mkdir(exist_ok=True)
    path = models_dir / "derivatives.joblib"
    joblib.dump(model, path)
    metadata = {"records": len(data), "experiment": experiment}
    with (models_dir / "derivatives.json").open("w") as fp:
        json.dump(metadata, fp)
    return path, metadata
