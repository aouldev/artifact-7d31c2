"""Repo-specific dynamic test runner profiles."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    yaml = None


@dataclass(frozen=True)
class CommandSpec:
    argv: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_ms: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandSpec":
        return cls(
            argv=[str(item) for item in data.get("argv", [])],
            cwd=str(data["cwd"]) if data.get("cwd") is not None else None,
            env={str(k): str(v) for k, v in dict(data.get("env") or {}).items()},
            timeout_ms=int(data["timeout_ms"]) if data.get("timeout_ms") is not None else None,
        )


@dataclass(frozen=True)
class InstallSpec:
    commands: list[CommandSpec] = field(default_factory=list)
    timeout_ms: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "InstallSpec":
        if not data:
            return cls()
        default_timeout = int(data["timeout_ms"]) if data.get("timeout_ms") is not None else None
        commands = []
        for command in data.get("commands") or []:
            spec = CommandSpec.from_dict(command)
            if spec.timeout_ms is None and default_timeout is not None:
                spec = CommandSpec(
                    argv=spec.argv,
                    cwd=spec.cwd,
                    env=spec.env,
                    timeout_ms=default_timeout,
                )
            commands.append(spec)
        return cls(commands=commands, timeout_ms=default_timeout)


@dataclass(frozen=True)
class RunnerSpec:
    name: str
    argv: list[str]
    path_regex: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_ms: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunnerSpec":
        return cls(
            name=str(data["name"]),
            argv=[str(item) for item in data.get("argv", [])],
            path_regex=str(data["path_regex"]) if data.get("path_regex") is not None else None,
            cwd=str(data["cwd"]) if data.get("cwd") is not None else None,
            env={str(k): str(v) for k, v in dict(data.get("env") or {}).items()},
            timeout_ms=int(data["timeout_ms"]) if data.get("timeout_ms") is not None else None,
        )

    def matches(self, test_file: str) -> bool:
        if not self.path_regex:
            return True
        return re.search(self.path_regex, test_file) is not None


@dataclass(frozen=True)
class ValidationSpec:
    compile: CommandSpec | None = None
    coverage: CommandSpec | None = None
    infer_compile: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ValidationSpec":
        if not data:
            return cls()

        def command_for(key: str) -> CommandSpec | None:
            raw = data.get(key)
            if not isinstance(raw, dict) or raw.get("enabled") is False:
                return None
            return CommandSpec.from_dict(raw)

        return cls(
            compile=command_for("compile"),
            coverage=command_for("coverage"),
            infer_compile=bool(data.get("infer_compile", True)),
        )


@dataclass(frozen=True)
class RepoProfile:
    repo: str
    image: str | None = None
    workspace: str = "/workspace/repo"
    env: dict[str, str] = field(default_factory=dict)
    install: InstallSpec = field(default_factory=InstallSpec)
    validation: ValidationSpec = field(default_factory=ValidationSpec)
    runners: list[RunnerSpec] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepoProfile":
        return cls(
            repo=str(data["repo"]),
            image=str(data["image"]) if data.get("image") is not None else None,
            workspace=str(data.get("workspace") or "/workspace/repo"),
            env={str(k): str(v) for k, v in dict(data.get("env") or {}).items()},
            install=InstallSpec.from_dict(data.get("install")),
            validation=ValidationSpec.from_dict(data.get("validation")),
            runners=[RunnerSpec.from_dict(item) for item in data.get("runners") or []],
        )

    def select_runner(self, test_file: str) -> RunnerSpec | None:
        for runner in self.runners:
            if runner.matches(test_file):
                return runner
        return None


def load_repo_profile(path: str | Path) -> RepoProfile:
    profile_path = Path(path)
    text = profile_path.read_text(encoding="utf-8")
    if profile_path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML repo profiles")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"invalid repo profile: {profile_path}")
    return RepoProfile.from_dict(data)


def load_repo_profiles(profile_dir: str | Path) -> dict[str, RepoProfile]:
    root = Path(profile_dir)
    if root.is_file():
        profile = load_repo_profile(root)
        return {profile.repo: profile}

    profiles: dict[str, RepoProfile] = {}
    for path in (
        sorted(root.glob("*.json")) + sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))
    ):
        profile = load_repo_profile(path)
        profiles[profile.repo] = profile
    return profiles


def render_template(value: str, context: dict[str, str]) -> str:
    rendered = value
    for key, replacement in context.items():
        rendered = rendered.replace("{{" + key + "}}", replacement)
    return rendered


def render_argv(argv: list[str], context: dict[str, str]) -> list[str]:
    return [render_template(item, context) for item in argv]
