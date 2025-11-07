"""QLib kısa vadeli ajan eğitim workflow'u."""

import json
from pathlib import Path
from typing import Tuple

import joblib
import qlib
from qlib.workflow import R
from qlib.workflow.task.gen_config import create_task

from app.config.settings import get_settings

settings = get_settings()


def run(symbol: str, start: str, end: str, experiment: str = "short_term") -> Tuple[Path, dict]:
    qlib.init(provider_uri=settings.qlib.data_path, region=settings.qlib.region)

    task = {
        "model": {
            "class": "LightGBM",
            "module_path": "qlib.contrib.model.gbdt",
            "kwargs": {
                "objective": "binary",
                "metric": "auc",
                "max_depth": 6,
                "num_leaves": 64,
            },
        },
        "dataset": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {
                "handler": {
                    "class": "Alpha158",
                    "module_path": "qlib.contrib.data.handler",
                    "kwargs": {
                        "start_time": start,
                        "end_time": end,
                        "instruments": symbol,
                    },
                },
                "segments": {
                    "train": (start, end),
                    "valid": (start, end),
                    "test": (start, end),
                },
            },
        },
    }

    task = create_task(task)
    with R.start(experiment_name=experiment):
        recorder = R.get_recorder()
        R.log_params(task=task)
        model = task["model"]()
        dataset = task["dataset"]()
        model.fit(dataset.get_split("train"))
        recorder.save_objects({"model": model})

        models_dir = Path("models")
        models_dir.mkdir(exist_ok=True)
        path = models_dir / "short_term.joblib"
        path.parent.mkdir(exist_ok=True)
        joblib.dump(model, path)
        metadata = {
            "symbol": symbol,
            "start": start,
            "end": end,
            "experiment": experiment,
        }
        with (models_dir / "short_term.json").open("w") as fp:
            json.dump(metadata, fp)
        return path, metadata
