#!/usr/bin/env python3
"""Run one ordered repository stream with persistent agent session and memory.

This is the public single-stream reference runner. It deliberately leaves
batch scheduling, provider switching, automatic recovery, and historical
checkpoint merging to private experiment infrastructure.
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
    public_episode_payload,
    render_template,
    reset_directory,
    run_process,
    session_id_from_codex_events,
    session_id_from_opencode_events,
)


PROMPT_PATH = Path(__file__).with_name("PROMPT.md")
FORBIDDEN_KEYS = {"gold", "ground_truth", "maintenance_label", "test_patch", "test_files"}


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    leaked = sorted(FORBIDDEN_KEYS & set(row))
    if leaked:
        raise ValueError(f"public episode contains forbidden fields: {leaked}")
    return row


def _prepare_episode(
    *,
    row: dict[str, Any],
    source_repo: Path,
    stream_root: Path,
    memory_root: Path,
    train_root: Path | None,
    agent: str,
    auth_path: Path | None,
    codex_bin: str,
    opencode_bin: str,
    dry_run: bool,
) -> tuple[Path, Path, Path, Path, list[str], dict[str, str], int, str]:
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
    public_episode = public_episode_payload(row)
    episode_path = episode_root / "episode.json"
    episode_path.write_text(json.dumps(public_episode, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prompt = render_template(PROMPT_PATH, row)
    (episode_root / "prompt.txt").write_text(prompt, encoding="utf-8")
    traces = episode_root / "traces"
    traces.mkdir()
    if agent == "codex":
        last_message = episode_root / "last_message.json"
        command = build_codex_command(
            codex_bin=codex_bin,
            workspace=workspace,
            episode=episode_path,
            input_root=input_root,
            memory_root=memory_root,
            traces=traces,
            mode="continuous",
            train_root=train_root,
            last_message=last_message,
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
                    mode="continuous",
                    episode=episode_path,
                    workspace=workspace,
                    input_root=input_root,
                    memory_root=memory_root,
                    traces=traces,
                    train_root=train_root,
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
    return episode_root, workspace, traces, input_root, command, environment, timeout, prompt


def run_stream(args: argparse.Namespace) -> dict[str, Any]:
    rows = [_public_row(row) for row in load_rows(args.episodes)]
    if args.repo:
        rows = [row for row in rows if str(row.get("repo")) == args.repo]
    if not rows:
        raise ValueError("no episodes remain after repository filtering")
    repos = {str(row.get("repo")) for row in rows}
    if len(repos) != 1:
        raise ValueError("Continuous reference runner accepts one repository stream at a time")
    repo = next(iter(repos))
    stream_root = (args.run_root / repo).resolve()
    if stream_root.exists() and not args.allow_existing:
        raise FileExistsError(stream_root)
    stream_root.mkdir(parents=True, exist_ok=True)
    (stream_root / "episodes").mkdir(exist_ok=True)
    workspace = stream_root / "workspace"
    workspace.mkdir(exist_ok=True)
    memory_root = stream_root / "memory"
    memory_root.mkdir(exist_ok=True)
    source_repo = (args.repos_root / repo).resolve()
    if not (source_repo / ".git").is_dir():
        raise FileNotFoundError(f"base repository is not a git checkout: {source_repo}")
    train_root = args.train_root.resolve() if args.train_root else None
    session_id: str | None = None
    summaries: list[dict[str, Any]] = []
    for row in rows[: args.limit or len(rows)]:
        prepared = _prepare_episode(
            row=row,
            source_repo=source_repo,
            stream_root=stream_root,
            memory_root=memory_root,
            train_root=train_root,
            agent=args.agent,
            auth_path=args.opencode_auth,
            codex_bin=args.codex_bin,
            opencode_bin=args.opencode_bin,
            dry_run=args.dry_run,
        )
        episode_root, workspace, traces, input_root, command, environment, timeout, prompt = prepared
        if session_id:
            if args.agent == "codex":
                command = build_codex_command(
                    codex_bin=args.codex_bin,
                    workspace=workspace,
                    episode=episode_root / "episode.json",
                    input_root=input_root,
                    memory_root=memory_root,
                    traces=traces,
                    mode="continuous",
                    train_root=train_root,
                    last_message=episode_root / "last_message.json",
                    session_id=session_id,
                )
            else:
                command.extend(["--session", session_id])
        (episode_root / "command.json").write_text(json.dumps(command, indent=2) + "\n", encoding="utf-8")
        if args.dry_run:
            summary = {"status": "dry_run", "episode_id": row["episode_id"], "agent": args.agent, "command": command}
            (episode_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
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
        stdout = (episode_root / "events.jsonl").read_text(encoding="utf-8")
        observed = None
        if result["status"] == "ok":
            try:
                observed = (
                    session_id_from_codex_events(stdout)
                    if args.agent == "codex"
                    else session_id_from_opencode_events(stdout)
                )
            except ValueError as exc:
                result.update({"status": "session_error", "error": str(exc)})
        if result["status"] == "ok" and not observed and not session_id:
            result.update({"status": "session_error", "error": "initial session ID was not emitted"})
        elif result["status"] == "ok" and session_id and observed and observed != session_id:
            result.update({"status": "session_error", "error": "resumed client emitted a different session ID"})
        elif result["status"] == "ok":
            session_id = observed or session_id
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
            "repo": repo,
            "agent": args.agent,
            "model": MODEL,
            "reasoning_effort": "low",
            "provider": "official_chatgpt_oauth",
            "session_id": session_id,
            "retry": "disabled",
        }
        (episode_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        summaries.append(summary)
        if result["status"] != "ok":
            break
    final = {"repo": repo, "agent": args.agent, "episodes": summaries, "session_id": session_id}
    (stream_root / "stream_summary.json").write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final, indent=2))
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "opencode"), required=True)
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--repos-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--train-root", type=Path)
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
