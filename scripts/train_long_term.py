import argparse

from app.agents.workflows.long_term_workflow import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("start")
    parser.add_argument("end")
    args = parser.parse_args()

    path, metadata = run(symbol=args.symbol, start=args.start, end=args.end)
    print(f"Model saved to {path} with metadata {metadata}")


if __name__ == "__main__":
    main()
