#!/usr/bin/env python3
"""Shared public runtime helpers for the Continuous and Online reference runners."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any


AGENTS_ROOT = Path(__file__).resolve().parent
STATEFUL_SERVER = AGENTS_ROOT / "stateful_server.py"
OUTPUT_SCHEMA = AGENTS_ROOT / "stateless" / "prediction.schema.json"
MODEL = "gpt-5.5"
REASONING_EFFORT = "low"
CODEX_VERSION = "codex-cli 0.144.6"
OPENCODE_VERSION = "1.17.18"
CODEX_TIMEOUT_SECONDS = 600
OPENCODE_TIMEOUT_SECONDS = 1800
DISABLED_CODEX_FEATURES = (
    "apps",
    "browser_use",
    "computer_use",
    "image_generation",
    "multi_agent",
    "shell_tool",
    "unified_exec",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("episode JSONL must contain objects")
            rows.append(value)
    return rows


def public_episode_payload(row: dict[str, Any]) -> dict[str, Any]:
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


def render_template(template_path: Path, row: dict[str, Any], *, memory_records: list[dict[str, Any]] | None = None) -> str:
    prompt = template_path.read_text(encoding="utf-8")
    prompt = prompt.replace(
        "{{EPISODE_JSON}}",
        json.dumps(public_episode_payload(row), ensure_ascii=False, indent=2),
    )
    if memory_records is not None:
        prompt = prompt.replace(
            "{{MEMORY_RECORDS_JSON}}",
            json.dumps(memory_records, ensure_ascii=False, indent=2),
        )
    return prompt


def materialize_base(source_repo: Path, base_commit: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    archive = subprocess.run(
        ["git", "-C", str(source_repo), "archive", base_commit],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as tar:
        tar.extractall(destination, filter="data")


def reset_directory(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"expected a directory: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def clean_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "ANTHROPIC_", "OPENCODE_"))
        and key != "CODEX_API_KEY"
    }


def toml_string(value: str) -> str:
    return json.dumps(value)


def toml_array(values: list[str]) -> str:
    return "[" + ",".join(toml_string(value) for value in values) + "]"


def server_args(
    *,
    mode: str,
    episode: Path,
    workspace: Path,
    input_root: Path,
    memory_root: Path,
    traces: Path,
    train_root: Path | None = None,
    memory_catalog: Path | None = None,
    read_only_repo: bool = False,
) -> list[str]:
    args = [
        str(STATEFUL_SERVER),
        "--mode",
        mode,
        "--episode",
        str(episode),
        "--repos-root",
        str(workspace),
        "--workspace-root",
        str(workspace / "repo"),
        "--input-root",
        str(input_root),
        "--memory-root",
        str(memory_root),
        "--traces-dir",
        str(traces),
    ]
    if train_root is not None:
        args.extend(["--train-root", str(train_root)])
    if memory_catalog is not None:
        args.extend(["--memory-catalog", str(memory_catalog)])
    if read_only_repo:
        args.append("--read-only-repo")
    return args


def build_codex_command(
    *,
    codex_bin: str,
    workspace: Path,
    episode: Path,
    input_root: Path,
    memory_root: Path,
    traces: Path,
    mode: str,
    train_root: Path | None = None,
    memory_catalog: Path | None = None,
    last_message: Path,
    session_id: str | None = None,
    read_only_repo: bool = False,
    output_schema: Path | None = OUTPUT_SCHEMA,
    include_mcp: bool = True,
) -> list[str]:
    if session_id:
        command = [codex_bin, "exec", "resume", session_id, "--skip-git-repo-check"]
    else:
        command = [
            codex_bin,
            "exec",
            "--cd",
            str(workspace),
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
        ]
        if mode != "continuous":
            command.append("--ephemeral")
    command.extend(
        [
            "--ignore-rules",
            "--ignore-user-config",
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
        ]
    )
    if output_schema is not None:
        insert_at = command.index("--output-last-message")
        if output_schema is not None:
            command[insert_at:insert_at] = ["--output-schema", str(output_schema)]
    if include_mcp:
        command.extend(
            [
                "-c",
                f"mcp_servers.repo_context.command={toml_string(os.environ.get('PYTHON', sys.executable))}",
                "-c",
                "mcp_servers.repo_context.args="
                + toml_array(
                    server_args(
                        mode=mode,
                        episode=episode,
                        workspace=workspace,
                        input_root=input_root,
                        memory_root=memory_root,
                        traces=traces,
                        train_root=train_root,
                        memory_catalog=memory_catalog,
                        read_only_repo=read_only_repo,
                    )
                ),
            ]
        )
    for feature in DISABLED_CODEX_FEATURES:
        command.extend(["--disable", feature])
    command.extend(["--model", MODEL, "-c", f"model_reasoning_effort={toml_string(REASONING_EFFORT)}", "-"])
    return command


def build_opencode_config(
    *,
    mode: str,
    episode: Path,
    workspace: Path,
    input_root: Path,
    memory_root: Path,
    traces: Path,
    train_root: Path | None = None,
    memory_catalog: Path | None = None,
    read_only_repo: bool = False,
    include_mcp: bool = True,
) -> dict[str, Any]:
    permissions = {"*": "deny", "repo_context_*": "allow"}
    config: dict[str, Any] = {
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
                        "variants": {REASONING_EFFORT: {"reasoningEffort": REASONING_EFFORT}},
                    }
                }
            }
        },
        "experimental": {"mcp_timeout": OPENCODE_TIMEOUT_SECONDS * 1000},
    }
    if include_mcp:
        config["mcp"] = {
            "repo_context": {
                "type": "local",
                "enabled": True,
                "command": [
                    os.environ.get("PYTHON", sys.executable),
                    *server_args(
                        mode=mode,
                        episode=episode,
                        workspace=workspace,
                        input_root=input_root,
                        memory_root=memory_root,
                        traces=traces,
                        train_root=train_root,
                        memory_catalog=memory_catalog,
                        read_only_repo=read_only_repo,
                    ),
                ],
            }
        }
    return config


def copy_opencode_auth(state_dir: Path, auth_path: Path | None = None) -> None:
    source = (auth_path or Path.home() / ".local/share/opencode-locator-oauth/data/opencode/auth.json").expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"official OpenCode OAuth auth file is required: {source}")
    target = state_dir / "data/opencode/auth.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    target.chmod(0o600)


def opencode_environment(state_dir: Path, config_path: Path) -> dict[str, str]:
    env = clean_env()
    env.update(
        {
            "XDG_DATA_HOME": str(state_dir / "data"),
            "XDG_CONFIG_HOME": str(state_dir / "config"),
            "XDG_CACHE_HOME": str(state_dir / "cache"),
            "XDG_STATE_HOME": str(state_dir / "state"),
            "OPENCODE_CONFIG": str(config_path),
            "OPENCODE_CONFIG_DIR": str(config_path.parent),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE_MCP": "true",
            "NO_COLOR": "1",
        }
    )
    return env


def session_id_from_codex_events(content: str) -> str | None:
    for line in content.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        candidates = [event]
        for key in ("payload", "thread", "session"):
            value = event.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        if event.get("type") in {"thread.started", "session.started"} or any(
            key in event for key in ("thread_id", "session_id")
        ):
            for candidate in candidates:
                for key in ("thread_id", "session_id", "id"):
                    value = candidate.get(key)
                    if isinstance(value, str) and value:
                        return value
    return None


def session_id_from_opencode_events(content: str) -> str | None:
    found: set[str] = set()
    for line in content.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        value = event.get("sessionID") or event.get("session_id")
        if isinstance(value, str) and value:
            found.add(value)
    if len(found) > 1:
        raise ValueError(f"OpenCode emitted multiple session IDs: {sorted(found)}")
    return next(iter(found), None)


def run_process(
    command: list[str],
    *,
    prompt: str,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=cwd,
            env=env,
            timeout=timeout,
            check=False,
        )
        stdout, stderr, status = completed.stdout, completed.stderr, (
            "ok" if completed.returncode == 0 else "process_error"
        )
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, status, returncode = exc.stdout or "", exc.stderr or "", "timeout", None
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "status": status,
        "returncode": returncode,
        "elapsed_seconds": time.monotonic() - started,
    }
