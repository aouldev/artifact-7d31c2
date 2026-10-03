#!/usr/bin/env python3
"""Prepare or run one stateless generation episode under the frozen protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))
from prediction_export import export_case

PROMPT_PATH = HERE / "PROMPT.md"
SCHEMA_PATH = HERE / "prediction.schema.json"
SERVER_PATH = HERE / "stateless_server.py"
MODEL = "gpt-5.5"
REASONING_EFFORT = "low"
TIMEOUT_SECONDS = 600
DISABLED_CODEX_FEATURES = (
    "apps",
    "browser_use",
    "computer_use",
    "image_generation",
    "multi_agent",
    "shell_tool",
    "unified_exec",
)
FORBIDDEN_EPISODE_KEYS = {
    "gold",
    "ground_truth",
    "maintenance_label",
    "test_patch",
    "test_diff",
    "test_files",
    "test_files_changed",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def toml_string(value: str) -> str:
    return json.dumps(value)


def toml_array(values: list[str]) -> str:
    return "[" + ",".join(toml_string(value) for value in values) + "]"


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("episode JSONL must contain objects")
            rows.append(row)
    return rows


def load_episode(path: Path, episode_id: str) -> dict[str, Any]:
    for row in load_rows(path):
        if str(row.get("episode_id")) == episode_id:
            leaked = sorted(FORBIDDEN_EPISODE_KEYS & set(row))
            if leaked:
                raise ValueError(f"public episode contains forbidden fields: {leaked}")
            return row
    raise KeyError(f"episode not found: {episode_id}")


def public_episode_payload(row: dict[str, Any]) -> dict[str, Any]:
    diff = str(row.get("prod_diff") or "")
    return {
        "episode_id": str(row["episode_id"]),
        "repo": str(row["repo"]),
        "base_commit": str(row["base_commit"]),
        "prod_files": [str(path) for path in row.get("prod_files") or []],
        "commit_message": str(row.get("commit_message") or ""),
        "timestamp": row.get("timestamp"),
        "prod_diff_path": "input/prod.diff",
        "prod_diff_bytes": len(diff.encode("utf-8")),
        "prod_diff_lines": len(diff.splitlines()),
    }


def render_prompt(row: dict[str, Any]) -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").replace(
        "{{EPISODE_JSON}}",
        json.dumps(public_episode_payload(row), ensure_ascii=False, indent=2),
    )


def materialize_base(source_repo: Path, base_commit: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    archive = subprocess.run(
        ["git", "-C", str(source_repo), "archive", base_commit],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as tar:
        tar.extractall(destination, filter="data")


def server_args(episode_path: Path, repos_root: Path, workspace: Path, traces: Path, input_root: Path) -> list[str]:
    return [
        str(SERVER_PATH),
        "--episode",
        str(episode_path),
        "--repos-root",
        str(repos_root),
        "--workspace-root",
        str(workspace / "repo"),
        "--input-root",
        str(input_root),
        "--traces-dir",
        str(traces),
    ]


def build_codex_command(
    *, codex_bin: str, workspace: Path, episode_path: Path, repos_root: Path,
    traces: Path, input_root: Path, last_message: Path,
) -> list[str]:
    command = [
        codex_bin,
        "exec",
        "--cd",
        str(workspace),
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-rules",
        "--ignore-user-config",
        "--output-schema",
        str(SCHEMA_PATH),
        "--output-last-message",
        str(last_message),
        "--json",
        "-c",
        'approval_policy="never"',
        "-c",
        'model_provider="openai"',
        "-c",
        'forced_login_method="chatgpt"',
        "-c",
        'web_search="disabled"',
        "-c",
        "features.memories=false",
        "-c",
        "model_context_window=258400",
        "-c",
        "model_auto_compact_token_limit=250000",
        "-c",
        f"mcp_servers.repo_context.command={toml_string(sys.executable)}",
        "-c",
        f"mcp_servers.repo_context.args={toml_array(server_args(episode_path, repos_root, workspace, traces, input_root))}",
    ]
    for feature in DISABLED_CODEX_FEATURES:
        command.extend(["--disable", feature])
    command.extend(["--model", MODEL, "-c", f"model_reasoning_effort={toml_string(REASONING_EFFORT)}", "-"])
    return command


def build_opencode_config(
    *, episode_path: Path, repos_root: Path, workspace: Path, traces: Path, input_root: Path
) -> dict[str, Any]:
    permissions = {"*": "deny", "repo_context_*": "allow"}
    return {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "snapshot": False,
        "enabled_providers": ["openai"],
        "permission": permissions,
        "provider": {
            "openai": {
                "models": {
                    MODEL: {
                        "options": {"reasoningEffort": REASONING_EFFORT},
                        "variants": {
                            REASONING_EFFORT: {"reasoningEffort": REASONING_EFFORT}
                        },
                    }
                }
            }
        },
        "mcp": {
            "repo_context": {
                "type": "local",
                "command": [sys.executable, *server_args(episode_path, repos_root, workspace, traces, input_root)],
                "enabled": True,
            }
        },
    }


def run_episode(args: argparse.Namespace) -> dict[str, Any]:
    row = load_episode(args.episode, args.episode_id)
    episode_id = str(row["episode_id"])
    run_dir = (args.run_root / episode_id).resolve()
    if run_dir.exists():
        if not args.exist_ok:
            raise FileExistsError(run_dir)
        if any(run_dir.iterdir()):
            raise FileExistsError(f"--exist-ok requires an empty run directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = run_dir / "workspace"
    input_root = workspace / "input"
    traces = run_dir / "traces"
    input_root.mkdir(parents=True, exist_ok=True)
    traces.mkdir(parents=True, exist_ok=True)
    source_repo = (args.repos_root / str(row["repo"])).resolve()
    if not (source_repo / ".git").is_dir():
        raise FileNotFoundError(f"base repository is not a git checkout: {source_repo}")
    materialize_base(source_repo, str(row["base_commit"]), workspace / "repo")
    (input_root / "prod.diff").write_text(str(row.get("prod_diff") or ""), encoding="utf-8")
    public_row = public_episode_payload(row)
    (run_dir / "episode.json").write_text(json.dumps(public_row, indent=2) + "\n", encoding="utf-8")
    prompt = render_prompt(row)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    config = json.loads((HERE / "experiment_config.json").read_text(encoding="utf-8"))
    config["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    config["prompt_template_sha256"] = sha256_file(PROMPT_PATH)
    (run_dir / "run_config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if args.agent == "codex":
        command = build_codex_command(
            codex_bin=args.codex_bin,
            workspace=workspace,
            episode_path=run_dir / "episode.json",
            repos_root=args.repos_root,
            traces=traces,
            input_root=input_root,
            last_message=run_dir / "last_message.json",
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OPENAI_", "ANTHROPIC_")) and key != "CODEX_API_KEY"
        }
    else:
        config_dir = run_dir / "opencode_config"
        config_dir.mkdir(exist_ok=True)
        state_dir = run_dir / "opencode_state"
        for name in ("data", "config", "cache", "state"):
            (state_dir / name).mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "opencode.json"
        config_path.write_text(json.dumps(build_opencode_config(
            episode_path=run_dir / "episode.json", repos_root=args.repos_root,
            workspace=workspace, traces=traces, input_root=input_root,
        ), indent=2) + "\n", encoding="utf-8")
        command = [
            args.opencode_bin,
            "run",
            "--auto",
            "--model",
            f"openai/{MODEL}",
            "--variant",
            REASONING_EFFORT,
            "--format",
            "json",
            "--dir",
            str(workspace),
        ]
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OPENAI_", "ANTHROPIC_", "OPENCODE_"))
            and key != "CODEX_API_KEY"
        }
        environment.update({
            "XDG_DATA_HOME": str(state_dir / "data"),
            "XDG_CONFIG_HOME": str(state_dir / "config"),
            "XDG_CACHE_HOME": str(state_dir / "cache"),
            "XDG_STATE_HOME": str(state_dir / "state"),
            "OPENCODE_CONFIG": str(config_path),
            "OPENCODE_CONFIG_DIR": str(config_dir),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE_MCP": "true",
            "NO_COLOR": "1",
        })
    (run_dir / "command.json").write_text(json.dumps(command, indent=2) + "\n", encoding="utf-8")
    if args.dry_run:
        print(json.dumps({"run_dir": str(run_dir), "agent": args.agent, "command": command, "retry": "disabled"}, indent=2))
        return {"status": "dry_run", "run_dir": str(run_dir)}
    if args.agent == "opencode":
        auth_path = (
            args.opencode_auth
            or Path.home() / ".local/share/opencode-locator-oauth/data/opencode/auth.json"
        ).expanduser()
        if not auth_path.is_file():
            raise FileNotFoundError(
                f"official OpenCode OAuth auth file is required: {auth_path}"
            )
        auth_target = run_dir / "opencode_state/data/opencode/auth.json"
        auth_target.parent.mkdir(parents=True, exist_ok=True)
        auth_target.write_bytes(auth_path.read_bytes())
        auth_target.chmod(0o600)
    started = time.monotonic()
    stdin_payload = prompt
    try:
        completed = subprocess.run(
            command,
            input=stdin_payload,
            text=True,
            capture_output=True,
            env=environment,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        (run_dir / "events.jsonl").write_text(stdout, encoding="utf-8")
        (run_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        summary = {
            "status": "timeout",
            "returncode": None,
            "elapsed_seconds": time.monotonic() - started,
            "agent": args.agent,
            "episode_id": episode_id,
            "provider": "official_chatgpt_oauth",
            "retry": "disabled",
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        raise SystemExit(124)
    (run_dir / "events.jsonl").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    status = "ok" if completed.returncode == 0 else "process_error"
    summary = {
        "status": status,
        "returncode": completed.returncode,
        "elapsed_seconds": time.monotonic() - started,
        "agent": args.agent,
        "episode_id": episode_id,
        "provider": "official_chatgpt_oauth",
        "retry": "disabled",
    }
    if status == "ok" and getattr(args, "export_predictions", False):
        try:
            export_case(agent=args.agent, episode=row, source_repo=source_repo,
                        workspace=workspace / "repo", case_root=run_dir,
                        output=run_dir / "predictions.jsonl")
            summary["predictions"] = "predictions.jsonl"
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            summary.update({"status": "export_error", "error": str(exc)})
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode or 1)
    if summary["status"] != "ok":
        raise SystemExit(1)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "opencode"), required=True)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--repos-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument(
        "--opencode-auth",
        type=Path,
        help="official OpenCode OAuth auth.json; required for a real OpenCode run",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--export-predictions", action="store_true",
                        help="Export workspace edits to evaluator predictions after a successful call")
    parser.add_argument(
        "--exist-ok",
        action="store_true",
        help="allow an existing run directory only when its workspace is still empty",
    )
    run_episode(parser.parse_args())


if __name__ == "__main__":
    main()
