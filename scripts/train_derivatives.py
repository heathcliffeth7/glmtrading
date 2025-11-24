import argparse

import pandas as pd

from app.agents.workflows.derivatives_workflow import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    args = parser.parse_args()

    data = pd.read_csv(args.csv_path)
    path, metadata = run(data)
    print(f"Derivatives model saved to {path} with metadata {metadata}")


if __name__ == "__main__":
    main()
