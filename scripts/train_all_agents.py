import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path


LOG_PATH = Path("logs/training.log")


def log(message: str) -> None:
    timestamp = datetime.utcnow().isoformat()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fp:
        fp.write(f"{timestamp} | {message}\n")


def run_command(cmd: list[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log(f"Running (attempt {attempt}/{max_attempts}): {' '.join(cmd)}")
        with LOG_PATH.open("a", encoding="utf-8") as log_fp:
            result = subprocess.run(cmd, stdout=log_fp, stderr=log_fp, text=True)
        if result.returncode == 0:
            log("Command succeeded")
            return
        wait = 2 ** attempt
        if attempt < max_attempts:
            log(f"Command failed with code {result.returncode}; retrying in {wait} seconds")
            time.sleep(wait)
        else:
            log(f"Command failed after {max_attempts} attempts with code {result.returncode}")
            raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output-dir", default="models")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    features_csv = Path("data/derivatives_features.csv")
    futures_csv = Path("data/binance_futures_metrics.csv")

    run_command([
        ".venv/bin/python", "scripts/export_derivatives_features.py", args.symbol, "5min", str(args.days),
        "--output", str(features_csv),
    ])
    run_command([
        ".venv/bin/python", "scripts/export_futures_metrics.py", args.symbol, str(args.days),
        "--output", str(futures_csv),
    ])
    run_command([
        ".venv/bin/python", "scripts/train_derivatives_pipeline.py", str(features_csv), str(futures_csv),
        "--output", str(output_dir / "derivatives.joblib"),
    ])
    derivatives_metrics = output_dir / "derivatives_backtest.json"
    run_command([
        ".venv/bin/python", "scripts/run_backtest.py", args.symbol, "5min", str(args.days * 24 * 12),
        "--agent", "derivatives", "--output", str(derivatives_metrics)
    ])
    short_term_metrics = output_dir / "short_term_backtest.json"
    long_term_metrics = output_dir / "long_term_backtest.json"

    run_command([
        ".venv/bin/python", "scripts/train_short_term.py", args.symbol, "2025-09-01", "2025-10-19"
    ])
    run_command([
        ".venv/bin/python", "scripts/run_backtest.py", args.symbol, "5min", str(args.days * 24 * 12),
        "--agent", "short_term", "--output", str(short_term_metrics)
    ])

    def evaluate(path: Path, name: str) -> None:
        payload = json.loads(path.read_text()) if path.exists() else {}
        metrics = payload.get("metrics", {})
        sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
        max_drawdown = float(metrics.get("max_drawdown", 0.0) or 0.0)
        log(f"Evaluating {name}: sharpe={sharpe:.4f}, max_drawdown={max_drawdown:.4f}")
        if sharpe < 1.0 or abs(max_drawdown) > 0.1:
            log(f"Model {name} failed thresholds (sharpe={sharpe}, max_drawdown={max_drawdown})")
            raise SystemExit(1)

    evaluate(derivatives_metrics, "derivatives")
    evaluate(short_term_metrics, "short_term")
    evaluate(long_term_metrics, "long_term")

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    def archive_model(name: str) -> None:
        src = output_dir / f"{name}.joblib"
        if not src.exists():
            print(f"Model {name} not found for archiving.")
            raise SystemExit(1)
        archive_dir = output_dir / "archive" / name
        archive_dir.mkdir(parents=True, exist_ok=True)
        dst = archive_dir / f"{timestamp}-{name}.joblib"
        shutil.copy2(src, dst)
        log(f"Archived {name} model to {dst}")

    archive_model("derivatives")
    archive_model("short_term")
    archive_model("long_term")

    log("All models passed performance thresholds. Restarting orchestrator...")
    run_command(["systemctl", "restart", "trading-orchestrator"])
    log("Orchestrator restarted successfully.")

    run_command([
        ".venv/bin/python", "scripts/train_long_term.py", args.symbol, "2025-03-01", "2025-10-19"
    ])
    run_command([
        ".venv/bin/python", "scripts/run_backtest.py", args.symbol, "4h", str(args.days * 6),
        "--agent", "long_term", "--output", str(long_term_metrics)
    ])


if __name__ == "__main__":
    main()
