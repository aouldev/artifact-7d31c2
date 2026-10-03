"""Private server-side CLI for official evaluation and admin tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark.server.evaluators.decision import evaluate_decision
from benchmark.server.evaluators.dynamic_prepare import prepare_dynamic_snapshots
from benchmark.server.evaluators.patch_dynamic import evaluate_patch_dynamic
from benchmark.server.evaluators.patch_static import evaluate_patch_static
from benchmark.server.evaluators.submission import (
    configure_storage_tool_cache,
    default_profiles_dir,
    default_snapshots_root,
    evaluate_submission,
    resolve_gold_path,
    server_storage_root,
)
from benchmark.server.tools.dynamic_runner import DynamicTestRunner
from benchmark.server.tools.repo_profile import load_repo_profile


def eval_decision_command(args: argparse.Namespace) -> None:
    metrics = evaluate_decision(
        predictions_path=args.predictions,
        gold_path=args.gold,
        output_path=args.output,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def eval_patch_static_command(args: argparse.Namespace) -> None:
    metrics = evaluate_patch_static(
        gold_path=args.gold,
        predictions_path=args.predictions,
        repos_root=args.repos_root,
        output_path=args.output,
        records_path=args.records_output,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def eval_patch_dynamic_command(args: argparse.Namespace) -> None:
    metrics = evaluate_patch_dynamic(
        gold_path=args.gold,
        predictions_path=args.predictions,
        repos_root=args.repos_root,
        profiles_dir=args.profiles_dir,
        snapshots_root=args.snapshots_root,
        output_dir=args.output_dir,
        install=not args.no_install,
        force_install=args.force_install,
        max_workers=args.max_workers,
        output_path=args.output,
        records_path=args.records_output,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def prepare_dynamic_command(args: argparse.Namespace) -> None:
    gold_path = resolve_gold_path(
        release_root=args.release_root,
        release_id=args.release_id,
        gold_path=args.gold,
    )
    storage_root = Path(args.storage_root or server_storage_root())
    configure_storage_tool_cache(storage_root)
    snapshots_root = Path(args.snapshots_root or default_snapshots_root(storage_root))
    release_name = args.release_id or (
        Path(args.release_root).name if args.release_root else gold_path.stem
    )
    output_dir = Path(args.output_dir or storage_root / "dynamic_runs" / f"prepare_{release_name}")
    summary_path = Path(args.output or output_dir / "prepare_summary.json")
    records_path = Path(args.records_output or output_dir / "prepare_records.jsonl")
    summary = prepare_dynamic_snapshots(
        gold_path=gold_path,
        repos_root=args.repos_root,
        profiles_dir=args.profiles_dir,
        snapshots_root=snapshots_root,
        output_dir=output_dir,
        install=not args.no_install,
        force_install=args.force_install,
        max_workers=args.max_workers,
        output_path=summary_path,
        records_path=records_path,
    )
    payload = {
        **summary,
        "artifacts": {
            "summary": str(summary_path),
            "records": str(records_path),
        },
        "storage": {
            "snapshots_root": str(snapshots_root),
            "node_modules_location": str(snapshots_root / "<episode_id>" / "repo" / "node_modules"),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def eval_submission_command(args: argparse.Namespace) -> None:
    result = evaluate_submission(
        release_root=args.release_root,
        release_id=args.release_id,
        gold_path=args.gold,
        submission_dir=args.submission_dir,
        predictions_path=args.predictions,
        repos_root=args.repos_root,
        profiles_dir=args.profiles_dir,
        storage_root=args.storage_root,
        output_dir=args.output_dir,
        snapshots_root=args.snapshots_root,
        dynamic_output_dir=args.dynamic_output_dir,
        run_dynamic=not args.skip_dynamic,
        prepare_dynamic=args.prepare_dynamic,
        install=not args.no_install,
        force_install=args.force_install,
        max_workers=args.max_workers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def run_test_command(args: argparse.Namespace) -> None:
    profile = load_repo_profile(args.profile)
    runner = DynamicTestRunner(
        profile=profile,
        repo_root=Path(args.repo_root),
        output_dir=Path(args.output_dir),
    )
    result = runner.run_test_file(args.test_file, install=args.install)
    output_path = runner.write_result(result)
    payload = result.to_dict()
    payload["output_path"] = str(output_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _add_gold_or_release_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--release-id",
        help="release id for data/datasets/<release_id>/private/",
    )
    parser.add_argument(
        "--release-root", help="official release root containing private/validation_gold.jsonl"
    )
    parser.add_argument("--gold", help="private gold JSONL; overrides --release-id/--release-root")


def _add_common_dynamic_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repos-root", default="repos", help="root directory for source repos")
    parser.add_argument(
        "--profiles-dir",
        default=str(default_profiles_dir()),
        help="repo profiles JSON/YAML directory",
    )
    parser.add_argument(
        "--storage-root", default=str(server_storage_root()), help="server storage root"
    )
    parser.add_argument("--snapshots-root", help="where reusable prepared snapshots live")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="skip dependency install; debugging only",
    )
    parser.add_argument(
        "--force-install", action="store_true", help="ignore install marker and reinstall"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.server",
        description="Private benchmark server/admin commands.",
    )
    subparsers = parser.add_subparsers(required=True)

    eval_parser = subparsers.add_parser("eval-decision", help="officially score predictions")
    eval_parser.add_argument("--gold", required=True, help="private gold JSONL")
    eval_parser.add_argument("--predictions", required=True, help="participant predictions JSONL")
    eval_parser.add_argument("--output")
    eval_parser.set_defaults(func=eval_decision_command)

    static_parser = subparsers.add_parser(
        "eval-patch-static",
        help="score TP patch presence and git-apply success against private gold",
    )
    static_parser.add_argument("--gold", required=True, help="private gold JSONL")
    static_parser.add_argument("--predictions", required=True, help="participant predictions JSONL")
    static_parser.add_argument(
        "--repos-root", required=True, help="root directory for source repos"
    )
    static_parser.add_argument("--output")
    static_parser.add_argument("--records-output")
    static_parser.set_defaults(func=eval_patch_static_command)

    dynamic_parser = subparsers.add_parser(
        "eval-patch-dynamic",
        help="run dynamic CSR/TPS validation on TP patches",
    )
    dynamic_parser.add_argument("--gold", required=True, help="private gold JSONL")
    dynamic_parser.add_argument(
        "--predictions", required=True, help="participant predictions JSONL"
    )
    dynamic_parser.add_argument(
        "--repos-root", required=True, help="root directory for source repos"
    )
    dynamic_parser.add_argument(
        "--profiles-dir", required=True, help="repo profiles JSON/YAML directory"
    )
    dynamic_parser.add_argument(
        "--snapshots-root", required=True, help="where reusable prepared snapshots live"
    )
    dynamic_parser.add_argument(
        "--output-dir", required=True, help="where dynamic run logs/results are written"
    )
    dynamic_parser.add_argument("--output")
    dynamic_parser.add_argument("--records-output")
    dynamic_parser.add_argument("--max-workers", type=int, default=1)
    dynamic_parser.add_argument(
        "--no-install",
        action="store_true",
        help="skip dependency install; debugging only",
    )
    dynamic_parser.add_argument(
        "--force-install", action="store_true", help="ignore install marker and reinstall"
    )
    dynamic_parser.set_defaults(func=eval_patch_dynamic_command)

    prepare_parser = subparsers.add_parser(
        "prepare-dynamic",
        help="prepare reusable snapshots and install dependencies for positive gold cases",
    )
    _add_gold_or_release_args(prepare_parser)
    _add_common_dynamic_args(prepare_parser)
    prepare_parser.add_argument("--output-dir", help="where prepare logs/results are written")
    prepare_parser.add_argument("--output", help="summary JSON path")
    prepare_parser.add_argument("--records-output", help="records JSONL path")
    prepare_parser.set_defaults(func=prepare_dynamic_command)

    submission_parser = subparsers.add_parser(
        "eval-submission",
        help="run decision, static patch, and dynamic patch evaluation into result.json",
    )
    _add_gold_or_release_args(submission_parser)
    submission_parser.add_argument(
        "--submission-dir", help="participant run directory containing predictions.jsonl"
    )
    submission_parser.add_argument(
        "--predictions", help="participant predictions JSONL; overrides --submission-dir"
    )
    _add_common_dynamic_args(submission_parser)
    submission_parser.add_argument(
        "--output-dir",
        help="evaluated result directory; defaults to storage/evaluated_results/<submission_id>",
    )
    submission_parser.add_argument(
        "--dynamic-output-dir", help="where dynamic run logs/results are written"
    )
    submission_parser.add_argument(
        "--skip-dynamic", action="store_true", help="run decision and static metrics only"
    )
    submission_parser.add_argument(
        "--prepare-dynamic",
        action="store_true",
        help="prewarm all positive gold snapshots before scoring this submission",
    )
    submission_parser.set_defaults(func=eval_submission_command)

    run_test_parser = subparsers.add_parser(
        "run-test",
        help="run one test file in a prepared repo worktree using a repo profile",
    )
    run_test_parser.add_argument("--profile", required=True, help="repo profile JSON/YAML path")
    run_test_parser.add_argument("--repo-root", required=True, help="prepared repo worktree root")
    run_test_parser.add_argument(
        "--test-file", required=True, help="test file path relative to repo root"
    )
    run_test_parser.add_argument("--output-dir", required=True)
    run_test_parser.add_argument(
        "--install",
        action="store_true",
        help="run profile install commands before executing the test command",
    )
    run_test_parser.set_defaults(func=run_test_command)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
