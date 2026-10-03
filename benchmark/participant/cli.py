"""Public CLI for participant-side benchmark runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark.participant.core.runner import BenchmarkRunner, RunConfig
from benchmark.participant.env import load_dotenv
from benchmark.participant.release import (
    DEFAULT_AGENT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RELEASE_ROOT,
    load_release,
    release_info,
)
from benchmark.participant.submission import package_submission, validate_submission


def json_print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def info_command(args: argparse.Namespace) -> None:
    release = load_release(args.release)
    json_print(release_info(release))


def run_command(args: argparse.Namespace) -> None:
    release = load_release(args.release)
    output_dir = Path(args.output_dir)
    config = RunConfig(
        train_path=release.train_path,
        episodes_path=release.validation_path,
        repos_root=Path(args.repos_root),
        output_dir=output_dir,
        agent_class=args.agent,
        run_id=args.run_id,
        limit=args.limit,
    )
    summary = BenchmarkRunner(config).run()
    summary["dataset_id"] = release.release_id
    summary["next_steps"] = {
        "validate": f"python -m benchmark validate {summary['run_dir']}",
        "package": f"python -m benchmark package {summary['run_dir']}",
    }
    json_print(summary)


def validate_command(args: argparse.Namespace) -> None:
    release = load_release(args.release)
    report = validate_submission(args.run_dir, release)
    json_print(report.to_dict())
    if not report.ok:
        raise SystemExit(1)


def package_command(args: argparse.Namespace) -> None:
    release = load_release(args.release)
    package_path = package_submission(
        args.run_dir,
        release,
        output=args.output,
        include_traces=not args.no_traces,
    )
    json_print({"ok": True, "package": str(package_path)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Run the static repo-level test-maintenance benchmark.",
    )
    parser.set_defaults(func=None)
    subparsers = parser.add_subparsers()

    info_parser = subparsers.add_parser("info", help="show the bundled public dataset")
    info_parser.add_argument("--release", default=str(DEFAULT_RELEASE_ROOT), help=argparse.SUPPRESS)
    info_parser.set_defaults(func=info_command)

    run_parser = subparsers.add_parser("run", help="run an agent on the bundled validation set")
    run_parser.add_argument("--agent", default=DEFAULT_AGENT, help="agent class as module:Class")
    run_parser.add_argument(
        "--repos-root", default="repos", help="root directory for local git repos"
    )
    run_parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="where runs are written"
    )
    run_parser.add_argument("--run-id", help="optional stable run directory name")
    run_parser.add_argument("--limit", type=int, help="run only the first N validation episodes")
    run_parser.add_argument("--release", default=str(DEFAULT_RELEASE_ROOT), help=argparse.SUPPRESS)
    run_parser.set_defaults(func=run_command)

    validate_parser = subparsers.add_parser(
        "validate", help="validate a run directory before sharing"
    )
    validate_parser.add_argument("run_dir", help="run directory produced by benchmark run")
    validate_parser.add_argument(
        "--release", default=str(DEFAULT_RELEASE_ROOT), help=argparse.SUPPRESS
    )
    validate_parser.set_defaults(func=validate_command)

    package_parser = subparsers.add_parser("package", help="zip a valid run directory")
    package_parser.add_argument("run_dir", help="run directory produced by benchmark run")
    package_parser.add_argument("--output", help="zip path; defaults to <run_dir>.zip")
    package_parser.add_argument(
        "--no-traces",
        action="store_true",
        help="omit traces from the zip; traces stay in the run directory",
    )
    package_parser.add_argument(
        "--release", default=str(DEFAULT_RELEASE_ROOT), help=argparse.SUPPRESS
    )
    package_parser.set_defaults(func=package_command)

    return parser


def main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    if args.func is None:
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
