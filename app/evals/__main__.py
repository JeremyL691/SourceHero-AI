"""CLI: `python -m app.evals run|download|export-feedback`."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evals")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="run the evaluation suites")
    run_cmd.add_argument(
        "--suite",
        default="all",
        help="all | retrieval | claims | verify | extraction | latency (comma-separated)",
    )
    run_cmd.add_argument("--mode", default="offline", choices=["offline", "full"])

    download = sub.add_parser("download", help="fetch the external datasets (needs network)")
    download.add_argument("--force", action="store_true")

    export = sub.add_parser("export-feedback", help="export user-labelled relations as gold data")
    export.add_argument("--out", default="evals/data/user_labeled_pairs.jsonl")

    args = parser.parse_args(argv)

    if args.command == "run":
        from app.evals.runner import run

        return run(args.suite, args.mode)

    if args.command == "download":
        from app.evals.datasets import download

        return download(force=args.force)

    if args.command == "export-feedback":
        from app.evals.datasets import export_feedback

        return export_feedback(args.out)

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
