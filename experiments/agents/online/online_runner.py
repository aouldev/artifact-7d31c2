#!/usr/bin/env python3
"""Run one repository stream under the host-managed Online memory boundary.

The actor receives an initial memory view and a read-only ``memory_read`` tool.
This public entry point does not synthesize or apply reflection updates; the
host-side reflection call is a separate step described in ``README.md``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

AGENTS_ROOT = Path(__file__).resolve().parents[1]
if str(AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTS_ROOT))
from prediction_export import export_case

from stateful_runtime import (  # noqa: E402
    CODEX_TIMEOUT_SECONDS,
    MODEL,
    OPENCODE_TIMEOUT_SECONDS,
    build_codex_command,
    build_opencode_config,
    clean_env,
    copy_opencode_auth,
    load_rows,
    materialize_base,
    opencode_environment,
    render_template,
    reset_directory,
    run_process,
)
from stateful_server import select_memory_records  # noqa: E402


PROMPT_PATH = Path(__file__).with_name("PROMPT.md")
FORBIDDEN_KEYS = {"gold", "ground_truth", "maintenance_label", "test_patch", "test_files"}


def load_catalog(path: Path, *, repo: str | None = None) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("memory_catalog") or payload.get("memory_records") or payload.get("records") or []
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError("memory catalog must be a JSON array of objects")
    records = [
        item["record"] if isinstance(item.get("record"), dict) else item for item in payload
    ]
    if repo is not None:
        foreign = sorted(
            str(item.get("repo"))
            for item in records
            if item.get("repo") and str(item.get("repo")) != repo
        )
        if foreign:
            raise ValueError(f"memory catalog contains records from another repository: {foreign}")
    return records


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    leaked = sorted(FORBIDDEN_KEYS & set(row))
    if leaked:
        raise ValueError(f"public episode contains forbidden fields: {leaked}")
    return row


def _episode_payload(row: dict[str, Any]) -> dict[str, Any]:
    diff = str(row.get("prod_diff") or "")
    return {
        "episode_id": str(row["episode_id"]),
        "repo": str(row["repo"]),
        "base_commit": str(row["base_commit"]),
        "prod_files": [str(path) for path in row.get("prod_files") or []],
        "commit_message": str(row.get("commit_message") or "") or None,
        "timestamp": row.get("timestamp"),
        "prod_diff_path": "input/prod.diff",
        "prod_diff_bytes": len(diff.encode("utf-8")),
        "prod_diff_lines": len(diff.splitlines()),
    }


def _prepare_episode(
    *,
    row: dict[str, Any],
    source_repo: Path,
    stream_root: Path,
    memory_root: Path,
    catalog_path: Path,
    catalog: list[dict[str, Any]],
    agent: str,
    codex_bin: str,
    opencode_bin: str,
    auth_path: Path | None,
    dry_run: bool,
) -> tuple[Path, Path, list[str], dict[str, str], int, str]:
    episode_id = str(row["episode_id"])
    episode_root = stream_root / "episodes" / episode_id
    if episode_root.exists():
        raise FileExistsError(f"episode output already exists: {episode_root}")
    episode_root.mkdir(parents=True)
    workspace = stream_root / "workspace"
    repo_root = workspace / "repo"
    input_root = workspace / "input"
    if repo_root.exists():
        shutil.rmtree(repo_root)
    reset_directory(input_root)
    materialize_base(source_repo, str(row["base_commit"]), repo_root)
    input_root.joinpath("prod.diff").write_text(str(row.get("prod_diff") or ""), encoding="utf-8")
    episode_path = episode_root / "episode.json"
    episode_path.write_text(
        json.dumps(_episode_payload(row), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    selected, omitted = select_memory_records(
        catalog,
        prod_files=[str(value) for value in row.get("prod_files") or []],
    )
    prompt = render_template(PROMPT_PATH, row, memory_records=selected)
    (episode_root / "prompt.txt").write_text(prompt, encoding="utf-8")
    (episode_root / "memory_context.json").write_text(
        json.dumps(
            {"memory_records": selected, "omitted_by_budget": omitted},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    traces = episode_root / "traces"
    traces.mkdir()
    if agent == "codex":
        command = build_codex_command(
            codex_bin=codex_bin,
            workspace=workspace,
            episode=episode_path,
            input_root=input_root,
            memory_root=memory_root,
            traces=traces,
            mode="online",
            memory_catalog=catalog_path,
            last_message=episode_root / "last_message.json",
        )
        environment = clean_env()
        timeout = CODEX_TIMEOUT_SECONDS
    else:
        state_dir = stream_root / "opencode_state"
        config_dir = stream_root / "opencode_config"
        for name in ("data", "config", "cache", "state"):
            (state_dir / name).mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "opencode.json"
        config_path.write_text(
            json.dumps(
                build_opencode_config(
                    mode="online",
                    episode=episode_path,
                    workspace=workspace,
                    input_root=input_root,
                    memory_root=memory_root,
                    traces=traces,
                    memory_catalog=catalog_path,
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not (state_dir / "data/opencode/auth.json").exists():
            if not dry_run or auth_path is not None:
                copy_opencode_auth(state_dir, auth_path)
        command = [
            opencode_bin,
            "run",
            "--auto",
            "--model",
            f"openai/{MODEL}",
            "--variant",
            "low",
            "--format",
            "json",
            "--dir",
            str(workspace),
        ]
        environment = opencode_environment(state_dir, config_path)
        timeout = OPENCODE_TIMEOUT_SECONDS
    return episode_root, workspace, command, environment, timeout, prompt


def run_stream(args: argparse.Namespace) -> dict[str, Any]:
    rows = [public_row(row) for row in load_rows(args.episodes)]
    rows = [row for row in rows if str(row.get("repo")) == args.repo]
    if not rows:
        raise ValueError("no episodes remain after repository filtering")
    if len({str(row.get("repo")) for row in rows}) != 1:
        raise ValueError("Online reference runner accepts one repository stream at a time")
    stream_root = (args.run_root / args.repo).resolve()
    if stream_root.exists() and not args.allow_existing:
        raise FileExistsError(stream_root)
    stream_root.mkdir(parents=True, exist_ok=True)
    (stream_root / "episodes").mkdir(exist_ok=True)
    memory_root = stream_root / "memory"
    memory_root.mkdir(exist_ok=True)
    catalog = load_catalog(args.memory_catalog, repo=args.repo)
    catalog_path = stream_root / "memory_catalog.json"
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    source_repo = (args.repos_root / args.repo).resolve()
    if not (source_repo / ".git").is_dir():
        raise FileNotFoundError(f"base repository is not a git checkout: {source_repo}")
    summaries: list[dict[str, Any]] = []
    for row in rows[: args.limit or len(rows)]:
        episode_root, workspace, command, environment, timeout, prompt = _prepare_episode(
            row=row,
            source_repo=source_repo,
            stream_root=stream_root,
            memory_root=memory_root,
            catalog_path=catalog_path,
            catalog=catalog,
            agent=args.agent,
            codex_bin=args.codex_bin,
            opencode_bin=args.opencode_bin,
            auth_path=args.opencode_auth,
            dry_run=args.dry_run,
        )
        (episode_root / "command.json").write_text(
            json.dumps(command, indent=2) + "\n", encoding="utf-8"
        )
        if args.dry_run:
            summary = {
                "status": "dry_run",
                "episode_id": str(row["episode_id"]),
                "repo": args.repo,
                "agent": args.agent,
                "memory_update": "external_host_step",
                "command": command,
            }
            (episode_root / "summary.json").write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8"
            )
            summaries.append(summary)
            continue
        result = run_process(
            command + ([prompt] if args.agent == "opencode" else []),
            prompt="" if args.agent == "opencode" else prompt,
            cwd=workspace,
            env=environment,
            timeout=timeout,
            stdout_path=episode_root / "events.jsonl",
            stderr_path=episode_root / "stderr.log",
        )
        if result["status"] == "ok" and getattr(args, "export_predictions", False):
            try:
                export_case(agent=args.agent, episode=row, source_repo=source_repo,
                            workspace=workspace / "repo", case_root=episode_root,
                            output=stream_root / "predictions.jsonl", append=True)
                result["predictions"] = "predictions.jsonl"
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                result.update({"status": "export_error", "error": str(exc)})
        summary = {
            **result,
            "episode_id": str(row["episode_id"]),
            "repo": args.repo,
            "agent": args.agent,
            "model": MODEL,
            "reasoning_effort": "low",
            "provider": "official_chatgpt_oauth",
            "memory_update": "external_host_step",
            "retry": "disabled",
        }
        (episode_root / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        summaries.append(summary)
        if result["status"] != "ok":
            break
    final = {"repo": args.repo, "agent": args.agent, "episodes": summaries}
    (stream_root / "stream_summary.json").write_text(
        json.dumps(final, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(final, indent=2))
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "opencode"), required=True)
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--repos-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--memory-catalog", type=Path, required=True)
    parser.add_argument("--opencode-auth", type=Path)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--export-predictions", action="store_true",
                        help="Save each patch before the next episode resets the workspace")
    parser.add_argument("--allow-existing", action="store_true")
    result = run_stream(parser.parse_args())
    if any(row["status"] not in {"ok", "dry_run"} for row in result["episodes"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
