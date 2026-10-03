#!/usr/bin/env python3
"""Run the host-side Online reflection writer for one completed episode.

The script only produces a candidate JSON update. It does not update a memory
catalog; a host must validate IDs, paths, causality, and source eligibility
before appending an accepted event.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

AGENTS_ROOT = Path(__file__).resolve().parents[1]
if str(AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTS_ROOT))

from stateful_runtime import (  # noqa: E402
    CODEX_TIMEOUT_SECONDS,
    MODEL,
    OPENCODE_TIMEOUT_SECONDS,
    build_codex_command,
    build_opencode_config,
    clean_env,
    copy_opencode_auth,
    opencode_environment,
    run_process,
)


PROMPT_PATH = Path(__file__).with_name("REFLECTION_PROMPT.md")
SCHEMA_PATH = Path(__file__).with_name("reflection_memory_updates.schema.json")
REQUIRED_FIELDS = {
    "operation",
    "target_memory_id",
    "memory_type",
    "anchor_path",
    "trigger",
    "claim",
    "inspect_paths",
    "links",
    "evidence_paths",
}


def _json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def render_prompt(
    *,
    episode: dict[str, Any],
    prod_diff: str,
    records: Any,
    observations: Any,
) -> str:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    replacements = {
        "{{EPISODE_JSON}}": json.dumps(episode, ensure_ascii=False, indent=2),
        "{{PROD_DIFF_TEXT}}": prod_diff,
        "{{RETRIEVED_RECORDS_JSON}}": json.dumps(records, ensure_ascii=False, indent=2),
        "{{BASE_OBSERVATIONS_JSON}}": json.dumps(observations, ensure_ascii=False, indent=2),
    }
    for marker, value in replacements.items():
        prompt = prompt.replace(marker, value)
    return prompt


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []
        index = 0
        while index < len(text):
            index = text.find("{", index)
            if index < 0:
                break
            try:
                candidate, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                index += 1
                continue
            if isinstance(candidate, dict):
                candidates.append(candidate)
            index += end
        if not candidates:
            raise ValueError("writer output does not contain a JSON object") from None
        value = candidates[-1]
    if not isinstance(value, dict):
        raise ValueError("writer output must be a JSON object")
    return value


def _opencode_text(events: str) -> str:
    texts: list[str] = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") if isinstance(event, dict) else None
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif isinstance(event, dict) and event.get("type") == "text" and isinstance(event.get("text"), str):
            texts.append(event["text"])
    if not texts:
        raise ValueError("OpenCode emitted no writer text")
    return texts[-1]


def validate_updates(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"memory_updates"}:
        raise ValueError("writer output must contain only memory_updates")
    updates = payload["memory_updates"]
    if not isinstance(updates, list) or len(updates) > 2:
        raise ValueError("memory_updates must contain at most two objects")
    for update in updates:
        if not isinstance(update, dict) or set(update) != REQUIRED_FIELDS:
            raise ValueError("memory update fields do not match the public schema")
        for key in ("operation", "target_memory_id", "memory_type", "anchor_path", "trigger", "claim"):
            if not isinstance(update[key], str):
                raise ValueError(f"{key} must be a string")
        if update["operation"] not in {"add", "revise", "retire"}:
            raise ValueError("invalid memory update operation")
        if update["memory_type"] not in {"semantic", "procedural", "episodic"}:
            raise ValueError("invalid memory update type")
        if not isinstance(update["claim"], str) or not 1 <= len(update["claim"]) <= 400:
            raise ValueError("memory claim must contain 1-400 characters")
        for key, limit in (("inspect_paths", 6), ("links", 4), ("evidence_paths", 8)):
            if not isinstance(update[key], list) or len(update[key]) > limit:
                raise ValueError(f"invalid {key} length")
        for key in ("inspect_paths", "evidence_paths"):
            if not all(isinstance(path, str) for path in update[key]):
                raise ValueError(f"{key} must contain strings")
        for link in update["links"]:
            if not isinstance(link, dict) or set(link) != {"relation", "target_path", "reason"}:
                raise ValueError("memory link fields do not match the public schema")
            if not all(isinstance(value, str) for value in link.values()):
                raise ValueError("memory link fields must be strings")
            if link["relation"] not in {"consumed_by", "observed_by", "co_maintained_with"}:
                raise ValueError("invalid memory link relation")
        if update["operation"] == "add" and update["target_memory_id"] != "":
            raise ValueError("add updates must have an empty target_memory_id")
        if update["operation"] in {"revise", "retire"} and not update["target_memory_id"]:
            raise ValueError("revise and retire updates require a target_memory_id")
    return payload


def run_writer(args: argparse.Namespace) -> dict[str, Any]:
    episode = _json_file(args.episode)
    if not isinstance(episode, dict):
        raise ValueError("episode must be a JSON object")
    prompt = render_prompt(
        episode=episode,
        prod_diff=args.prod_diff.read_text(encoding="utf-8"),
        records=_json_file(args.retrieved_records),
        observations=_json_file(args.base_observations),
    )
    if args.agent == "opencode":
        prompt += "\n\nRequired output JSON schema:\n" + SCHEMA_PATH.read_text(encoding="utf-8")
    run_root = args.run_root.resolve()
    if run_root.exists() and any(run_root.iterdir()) and not args.allow_existing:
        raise FileExistsError(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = run_root / "workspace"
    workspace.mkdir(exist_ok=True)
    (run_root / "episode.json").write_text(
        json.dumps(episode, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_root / "prompt.txt").write_text(prompt, encoding="utf-8")
    if args.agent == "codex":
        command = build_codex_command(
            codex_bin=args.codex_bin,
            workspace=workspace,
            episode=run_root / "episode.json",
            input_root=workspace,
            memory_root=workspace,
            traces=run_root / "traces",
            mode="online",
            last_message=run_root / "last_message.json",
            output_schema=SCHEMA_PATH,
            include_mcp=False,
        )
        environment = clean_env()
        timeout = CODEX_TIMEOUT_SECONDS
    else:
        state_dir = run_root / "opencode_state"
        config_dir = run_root / "opencode_config"
        for name in ("data", "config", "cache", "state"):
            (state_dir / name).mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "opencode.json"
        config_path.write_text(
            json.dumps(
                build_opencode_config(
                    mode="online",
                    episode=run_root / "episode.json",
                    workspace=workspace,
                    input_root=workspace,
                    memory_root=workspace,
                    traces=run_root / "traces",
                    include_mcp=False,
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not (state_dir / "data/opencode/auth.json").exists():
            if not args.dry_run or args.opencode_auth is not None:
                copy_opencode_auth(state_dir, args.opencode_auth)
        command = [
            args.opencode_bin,
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
    (run_root / "command.json").write_text(json.dumps(command, indent=2) + "\n", encoding="utf-8")
    if args.dry_run:
        summary = {"status": "dry_run", "agent": args.agent, "command": command}
        (run_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return summary
    result = run_process(
        command + ([prompt] if args.agent == "opencode" else []),
        prompt="" if args.agent == "opencode" else prompt,
        cwd=workspace,
        env=environment,
        timeout=timeout,
        stdout_path=run_root / "events.jsonl",
        stderr_path=run_root / "stderr.log",
    )
    payload: dict[str, Any] | None = None
    parse_error: str | None = None
    if result["status"] == "ok":
        try:
            text = (
                (run_root / "last_message.json").read_text(encoding="utf-8")
                if args.agent == "codex"
                else _opencode_text((run_root / "events.jsonl").read_text(encoding="utf-8"))
            )
            payload = validate_updates(_parse_json_object(text))
            (run_root / "updates.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parse_error = str(exc)
            result = {**result, "status": "parse_failure"}
    summary = {**result, "agent": args.agent, "accepted_by_host": False, "parse_error": parse_error}
    (run_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "opencode"), required=True)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--prod-diff", type=Path, required=True)
    parser.add_argument("--retrieved-records", type=Path, required=True)
    parser.add_argument("--base-observations", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--opencode-auth", type=Path)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-existing", action="store_true")
    run_writer(parser.parse_args())


if __name__ == "__main__":
    main()
