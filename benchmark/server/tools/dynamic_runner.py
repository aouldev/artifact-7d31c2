"""Dynamic test execution skeleton for benchmark scoring."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.participant.core.paths import get_shims_path
from benchmark.server.tools.prepared_snapshot import dependency_manifests_unchanged
from benchmark.server.tools.repo_profile import (
    CommandSpec,
    RepoProfile,
    render_argv,
    render_template,
)

DEFAULT_INSTALL_TIMEOUT_MS = 1_200_000
DEFAULT_BUILD_TIMEOUT_MS = 1_200_000


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    cwd: str
    exit_code: int | None
    timed_out: bool
    elapsed_ms: float
    stdout: str = ""
    stderr: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandResult":
        exit_code = data.get("exit_code")
        return cls(
            argv=[str(item) for item in data.get("argv") or []],
            cwd=str(data.get("cwd") or ""),
            exit_code=int(exit_code) if exit_code is not None else None,
            timed_out=bool(data.get("timed_out")),
            elapsed_ms=float(data.get("elapsed_ms") or 0.0),
            stdout=(
                data.get("stdout", b"").decode(errors="replace")
                if isinstance(data.get("stdout"), bytes)
                else str(data.get("stdout") or "")
            ),
            stderr=(
                data.get("stderr", b"").decode(errors="replace")
                if isinstance(data.get("stderr"), bytes)
                else str(data.get("stderr") or "")
            ),
        )

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "elapsed_ms": self.elapsed_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class TestRunResult:
    repo: str
    test_file: str
    runner: str | None
    status: str
    install_results: list[CommandResult] = field(default_factory=list)
    test_result: CommandResult | None = None
    attempts: list[CommandResult] = field(default_factory=list)
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TestRunResult":
        test_result = data.get("test_result")
        return cls(
            repo=str(data.get("repo") or ""),
            test_file=str(data.get("test_file") or ""),
            runner=str(data["runner"]) if data.get("runner") is not None else None,
            status=str(data.get("status") or ""),
            install_results=[
                CommandResult.from_dict(item) for item in data.get("install_results") or []
            ],
            test_result=(
                CommandResult.from_dict(test_result) if isinstance(test_result, dict) else None
            ),
            attempts=[CommandResult.from_dict(item) for item in data.get("attempts") or []],
            error=str(data["error"]) if data.get("error") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "test_file": self.test_file,
            "runner": self.runner,
            "status": self.status,
            "install_results": [item.to_dict() for item in self.install_results],
            "test_result": self.test_result.to_dict() if self.test_result else None,
            "attempts": [item.to_dict() for item in self.attempts],
            "error": self.error,
        }


@dataclass(frozen=True)
class ValidationCommandResult:
    name: str
    status: str
    result: CommandResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "result": self.result.to_dict() if self.result else None,
        }


class DynamicTestRunner:
    """Runs repo-specific test commands in an already prepared worktree.

    This class intentionally does not create Docker containers yet. It is the command/profile
    layer shared by local development and a future Docker-backed worker.
    """

    def __init__(self, profile: RepoProfile, repo_root: str | Path, output_dir: str | Path) -> None:
        self.profile = profile
        self.repo_root = Path(repo_root).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._prepare_output_assets()

    def _prepare_output_assets(self) -> None:
        shims_dir = get_shims_path()
        if not shims_dir.exists():
            return
        for source in shims_dir.iterdir():
            if not source.is_file():
                continue
            target = self.output_dir / source.name
            if target.exists() and target.read_bytes() == source.read_bytes():
                continue
            shutil.copyfile(source, target)

    def _clear_coverage_artifacts(self) -> None:
        """Remove coverage outputs from an earlier attempt of this test file."""

        for coverage_dir in sorted(self.output_dir.rglob("coverage"), reverse=True):
            if coverage_dir.is_symlink():
                coverage_dir.unlink(missing_ok=True)
            elif coverage_dir.is_dir():
                shutil.rmtree(coverage_dir, ignore_errors=True)
        for pattern in ("coverage-*.json", "coverage-final.json", "lcov.info"):
            for artifact in self.output_dir.rglob(pattern):
                if artifact.is_file() or artifact.is_symlink():
                    artifact.unlink(missing_ok=True)

    def _tool_cache_root(self) -> Path:
        return Path(
            os.environ.get(
                "REPO_TEST_EVOLUTION_TOOL_CACHE",
                str(Path.home() / ".cache" / "repo-test-evolution" / "tools"),
            )
        )

    def _default_env(self) -> dict[str, str]:
        cache_root = self._tool_cache_root()
        env = {
            "CI": "1",
            "TZ": "UTC",
            "COREPACK_HOME": str(cache_root / "corepack"),
            "XDG_CACHE_HOME": str(cache_root / "xdg"),
            "npm_config_cache": str(cache_root / "npm"),
            "YARN_CACHE_FOLDER": str(cache_root / "yarn"),
            "PNPM_HOME": str(cache_root / "pnpm-home"),
            "PNPM_STORE_PATH": str(cache_root / "pnpm-store"),
        }
        return env

    def _render_env_value(self, value: str, context: dict[str, str], env: dict[str, str]) -> str:
        rendered = render_template(value, context)
        for key, replacement in env.items():
            rendered = rendered.replace("${" + key + "}", replacement)
            rendered = rendered.replace("$" + key, replacement)
        return rendered

    def _package_json(self, directory: Path | None = None) -> dict[str, Any] | None:
        package_json = (directory or self.repo_root) / "package.json"
        if not package_json.exists():
            return None
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _package_name(self, directory: Path) -> str | None:
        package_json = self._package_json(directory)
        name = package_json.get("name") if package_json else None
        return str(name) if isinstance(name, str) and name else None

    def _package_manager_spec(self) -> str:
        package_json = self._package_json() or {}
        package_manager = package_json.get("packageManager")
        return str(package_manager) if package_manager else ""

    def _package_manager(self) -> str | None:
        package_manager = self._package_manager_spec()
        if package_manager.startswith("pnpm@"):
            return "pnpm"
        if package_manager.startswith("yarn@"):
            return "yarn"
        if package_manager.startswith("npm@"):
            return "npm"
        if (self.repo_root / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (self.repo_root / "yarn.lock").exists():
            return "yarn"
        if (self.repo_root / "package-lock.json").exists() or (
            self.repo_root / "npm-shrinkwrap.json"
        ).exists():
            return "npm"
        if (self.repo_root / "package.json").exists():
            return "npm"
        return None

    def _package_manager_prefix(self) -> list[str]:
        package_manager = self._package_manager()
        if package_manager == "pnpm":
            return ["corepack", "pnpm", "exec"]
        if package_manager == "yarn":
            return ["corepack", "yarn"]
        if package_manager == "npm":
            return ["npm", "exec", "--"]
        return []

    def _package_manager_workspace_prefix(self) -> list[str]:
        package_manager = self._package_manager()
        if package_manager == "pnpm":
            return ["corepack", "pnpm"]
        if package_manager == "yarn":
            return ["corepack", "yarn"]
        if package_manager == "npm":
            return []
        return []

    def _package_manager_run_prefix(self) -> list[str]:
        package_manager = self._package_manager()
        if package_manager == "pnpm":
            return ["corepack", "pnpm"]
        if package_manager == "yarn":
            return ["corepack", "yarn"]
        if package_manager == "npm":
            return ["npm"]
        return []

    def _workspace_exec_prefix(self, package_name: str) -> list[str]:
        package_manager = self._package_manager()
        if package_manager == "pnpm":
            return ["corepack", "pnpm", "--filter", package_name, "exec"]
        if package_manager == "yarn":
            return ["corepack", "yarn", "workspace", package_name, "exec"]
        return []

    def _inferred_install_commands(self) -> list[CommandSpec]:
        package_manager = self._package_manager()
        if package_manager is None:
            return []
        timeout_ms = self.profile.install.timeout_ms or DEFAULT_INSTALL_TIMEOUT_MS
        commands: list[CommandSpec] = []
        if package_manager in {"pnpm", "yarn"}:
            commands.append(
                CommandSpec(
                    argv=["corepack", "enable"],
                    cwd="{{repo_root}}",
                    timeout_ms=120000,
                )
            )
        if package_manager == "pnpm":
            commands.append(
                CommandSpec(
                    argv=["corepack", "pnpm", "install", "--frozen-lockfile"],
                    cwd="{{repo_root}}",
                    timeout_ms=timeout_ms,
                )
            )
        elif package_manager == "yarn":
            package_manager_spec = self._package_manager_spec()
            immutable_flag = (
                "--frozen-lockfile" if package_manager_spec.startswith("yarn@1.") else "--immutable"
            )
            commands.append(
                CommandSpec(
                    argv=["corepack", "yarn", "install", immutable_flag],
                    cwd="{{repo_root}}",
                    timeout_ms=timeout_ms,
                )
            )
        elif package_manager == "npm":
            install_verb = "ci" if (self.repo_root / "package-lock.json").exists() else "install"
            commands.append(
                CommandSpec(
                    argv=["npm", install_verb],
                    cwd="{{repo_root}}",
                    timeout_ms=timeout_ms,
                )
            )
        return commands

    def install_commands(self) -> list[CommandSpec]:
        return self.profile.install.commands or self._inferred_install_commands()

    def _inferred_compile_command(self) -> CommandSpec | None:
        package_json = self._package_json()
        scripts = package_json.get("scripts") if package_json else None
        if not isinstance(scripts, dict):
            return None
        for script_name in ("typecheck", "type-check", "build:code", "build"):
            if isinstance(scripts.get(script_name), str):
                prefix = self._package_manager_run_prefix()
                if not prefix:
                    return None
                return CommandSpec(
                    argv=[*prefix, "run", script_name],
                    cwd="{{repo_root}}",
                    timeout_ms=DEFAULT_BUILD_TIMEOUT_MS,
                )
        return None

    def _file_sha256(self, rel_path: str) -> str | None:
        path = self.repo_root / rel_path
        if not path.exists() or not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def install_marker_payload(self, prepared_commit: str) -> dict[str, Any]:
        commands = self.install_commands()
        return {
            "profile_repo": self.profile.repo,
            "prepared_commit": prepared_commit,
            "install_commands": [command.argv for command in commands],
            "install_cwd": [command.cwd for command in commands],
            "profile_env": self.profile.env,
            "package_files": {
                name: digest
                for name in (
                    "package.json",
                    "pnpm-lock.yaml",
                    "yarn.lock",
                    "package-lock.json",
                    "npm-shrinkwrap.json",
                )
                if (digest := self._file_sha256(name)) is not None
            },
        }

    def _nearest_package_dir(self, test_file: str) -> Path:
        if not test_file:
            return self.repo_root
        candidate = (self.repo_root / test_file).resolve().parent
        while True:
            if (candidate / "package.json").exists():
                return candidate
            if candidate == self.repo_root or self.repo_root not in candidate.parents:
                return self.repo_root
            candidate = candidate.parent

    def _relative_to_repo(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.repo_root))
        except ValueError:
            return str(path)

    def _jest_config_args(self, package_dir: Path) -> list[str]:
        candidates = [
            package_dir / "jest.config.ts",
            package_dir / "jest.config.js",
            package_dir / ".jestconfig.json",
            self.repo_root / "jest.config.ts",
            self.repo_root / "jest.config.js",
            self.repo_root / ".jestconfig.json",
        ]
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if candidate.exists():
                return ["--config", str(candidate)]
        return []

    def _root_script_exists(self, script_name: str) -> bool:
        package_json = self._package_json(self.repo_root)
        scripts = package_json.get("scripts") if package_json else None
        return isinstance(scripts, dict) and isinstance(scripts.get(script_name), str)

    def _auto_vitest_commands(self, test_file: str, context: dict[str, str]) -> list[CommandSpec]:
        prefix = self._package_manager_prefix()
        if not prefix:
            return []
        package_dir = self._nearest_package_dir(test_file)
        package_name = self._package_name(package_dir)
        package_dir_template = self._relative_to_repo(package_dir)
        output_dir = context["output_dir"]
        provider_dir = f"{output_dir}/coverage/vitest_provider"
        root_provider_dir = f"{output_dir}/coverage/vitest_root_provider"
        raw_dir = f"{output_dir}/coverage/vitest_raw_v8"
        commands: list[CommandSpec] = []
        if package_name and package_dir != self.repo_root:
            workspace_prefix = self._workspace_exec_prefix(package_name)
            if workspace_prefix:
                commands.append(
                    CommandSpec(
                        argv=[
                            *workspace_prefix,
                            "vitest",
                            "run",
                            "{{test_file}}",
                            "--coverage.enabled",
                            "--coverage.reporter=json",
                            "--coverage.reporter=lcov",
                            "--coverage.reportsDirectory",
                            provider_dir,
                        ],
                        cwd="{{repo_root}}",
                    )
                )
        commands.extend(
            [
                CommandSpec(
                    argv=[
                        *prefix,
                        "vitest",
                        "run",
                        "{{test_file}}",
                        "--coverage.enabled",
                        "--coverage.reporter=json",
                        "--coverage.reporter=lcov",
                        "--coverage.reportsDirectory",
                        provider_dir,
                    ],
                    cwd=package_dir_template,
                ),
                CommandSpec(
                    argv=[
                        *prefix,
                        "vitest",
                        "run",
                        "{{test_file}}",
                        "--coverage.enabled",
                        "--coverage.reporter=json",
                        "--coverage.reporter=lcov",
                        "--coverage.reportsDirectory",
                        root_provider_dir,
                    ],
                    cwd="{{repo_root}}",
                ),
                CommandSpec(
                    argv=[
                        *prefix,
                        "vitest",
                        "run",
                        "{{test_file}}",
                        "--pool",
                        "threads",
                        "--no-file-parallelism",
                        "--no-isolate",
                    ],
                    cwd=package_dir_template,
                    env={"NODE_V8_COVERAGE": raw_dir},
                ),
            ]
        )
        return commands

    def _auto_jest_commands(self, test_file: str, context: dict[str, str]) -> list[CommandSpec]:
        prefix = self._package_manager_prefix()
        if not prefix:
            return []
        package_dir = self._nearest_package_dir(test_file)
        package_name = self._package_name(package_dir)
        package_dir_template = self._relative_to_repo(package_dir)
        config_args = self._jest_config_args(package_dir)
        root_coverage_dir = f"{context['output_dir']}/coverage/jest_root"
        local_coverage_dir = f"{context['output_dir']}/coverage/jest_local"
        commands: list[CommandSpec] = []
        root_command = [
            *prefix,
            "jest",
            "--runInBand",
            "--runTestsByPath",
            "{{test_file}}",
            "--coverage",
            "--coverageReporters=json",
            "--coverageReporters=lcov",
            "--coverageDirectory",
            root_coverage_dir,
            *config_args,
        ]
        if self._root_script_exists("test:server"):
            commands.append(
                CommandSpec(
                    argv=[
                        *prefix,
                        "test:server",
                        "--runInBand",
                        "--runTestsByPath",
                        "{{test_file}}",
                        "--coverage",
                        "--coverageReporters=json",
                        "--coverageReporters=lcov",
                        "--coverageDirectory",
                        root_coverage_dir,
                    ],
                    cwd="{{repo_root}}",
                )
            )
        commands.extend(
            [
                CommandSpec(argv=root_command, cwd="{{repo_root}}"),
                CommandSpec(
                    argv=[
                        *prefix,
                        "jest",
                        "--runInBand",
                        "--runTestsByPath",
                        "{{test_name}}",
                        "--coverage",
                        "--coverageReporters=json",
                        "--coverageReporters=lcov",
                        "--coverageDirectory",
                        local_coverage_dir,
                        *config_args,
                    ],
                    cwd=package_dir_template,
                ),
            ]
        )
        if self._root_script_exists("test"):
            commands.append(
                CommandSpec(
                    argv=[
                        *prefix,
                        "test",
                        "--runInBand",
                        "--runTestsByPath",
                        "{{test_file}}",
                        "--coverage",
                        "--coverageReporters=json",
                        "--coverageReporters=lcov",
                        "--coverageDirectory",
                        root_coverage_dir,
                    ],
                    cwd="{{repo_root}}",
                )
            )
        if package_name and package_dir != self.repo_root:
            workspace_prefix = self._workspace_exec_prefix(package_name)
            if workspace_prefix:
                commands.append(
                    CommandSpec(
                        argv=[
                            *workspace_prefix,
                            "jest",
                            "--runInBand",
                            "--runTestsByPath",
                            "{{test_file}}",
                            "--coverage",
                            "--coverageReporters=json",
                            "--coverageReporters=lcov",
                            "--coverageDirectory",
                            root_coverage_dir,
                            *config_args,
                        ],
                        cwd="{{repo_root}}",
                    )
                )
        return commands

    def _auto_test_commands(
        self, test_file: str, context: dict[str, str]
    ) -> list[tuple[str, CommandSpec]]:
        test_path = test_file.lower()
        # Do not guess a unit-test runner for browser suites. A missing repo
        # profile is an infrastructure classification, not a reason to invoke
        # Jest against a Playwright/Cypress file.
        if any(segment in test_path.split("/") for segment in ("playwright", "cypress")) or ".e2e." in test_path:
            return []
        commands: list[tuple[str, CommandSpec]] = []
        if "vitest" in test_path or test_path.endswith(
            (".spec.ts", ".test.ts", ".spec.tsx", ".test.tsx", ".spec.js", ".test.js")
        ):
            commands.extend(
                (f"auto_vitest_{index}", command)
                for index, command in enumerate(
                    self._auto_vitest_commands(test_file, context), start=1
                )
            )
        commands.extend(
            (f"auto_jest_{index}", command)
            for index, command in enumerate(self._auto_jest_commands(test_file, context), start=1)
        )
        return commands

    def _context(self, test_file: str) -> dict[str, str]:
        test_path = Path(test_file)
        absolute_test_path = (self.repo_root / test_file).resolve() if test_file else self.repo_root
        nearest_package_dir = self._nearest_package_dir(test_file)
        test_dir = "" if not test_file or str(test_path.parent) == "." else str(test_path.parent)
        if test_file:
            try:
                test_file_from_cwd = str(absolute_test_path.relative_to(nearest_package_dir))
            except ValueError:
                test_file_from_cwd = test_file
        else:
            test_file_from_cwd = ""
        context = {
            "repo_root": str(self.repo_root),
            "repoRoot": str(self.repo_root),
            "workspace": self.profile.workspace,
            "test_file": test_file,
            "test_file_abs": str(absolute_test_path),
            "test_file_from_cwd": test_file_from_cwd,
            "test_dir": test_dir,
            "test_name": test_path.name,
            "nearest_package_dir": str(nearest_package_dir),
            "nearestPackageDir": str(nearest_package_dir),
            "assetPath": test_file,
            "assetPathAbs": str(absolute_test_path),
            "assetPathFromCwd": test_file_from_cwd,
            "coverageDir": str(self.output_dir),
            "output_dir": str(self.output_dir),
            "run_dir": str(self.output_dir),
            "sandboxDir": str(self.output_dir),
        }
        return context

    def context(self, test_file: str = "") -> dict[str, str]:
        return self._context(test_file)

    def _run_command(self, spec: CommandSpec, context: dict[str, str]) -> CommandResult:
        argv = render_argv(spec.argv, context)
        # Vitest cleans reportsDirectory before spawning workers. Profiles that
        # use output_dir for both coverage and NODE_OPTIONS preload assets must
        # not let that cleanup remove their own worker shims.
        coverage_flag = "--coverage.reportsDirectory"
        for index, arg in enumerate(argv):
            if arg == coverage_flag and index + 1 < len(argv):
                if argv[index + 1] == str(self.output_dir):
                    argv[index + 1] = str(self.output_dir / "coverage" / "vitest")
            elif arg == f"{coverage_flag}={self.output_dir}":
                argv[index] = f"{coverage_flag}={self.output_dir / 'coverage' / 'vitest'}"
        cwd_template = spec.cwd or "{{repo_root}}"
        cwd = render_template(cwd_template, context)
        if cwd and not Path(cwd).is_absolute():
            cwd = str((self.repo_root / cwd).resolve())
        env = os.environ.copy()
        for key, value in self._default_env().items():
            env.setdefault(key, value)
        for key, value in self.profile.env.items():
            env[key] = self._render_env_value(value, context, env)
        for key, value in spec.env.items():
            env[key] = self._render_env_value(value, context, env)
        timeout_seconds = (spec.timeout_ms or 600000) / 1000
        started = time.time()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout_seconds)
                return CommandResult(
                    argv=argv,
                    cwd=cwd,
                    exit_code=proc.returncode,
                    timed_out=False,
                    elapsed_ms=(time.time() - started) * 1000,
                    stdout=stdout,
                    stderr=stderr,
                )
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, 15)
                    stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, 9)
                    stdout, stderr = proc.communicate()
                return CommandResult(
                    argv=argv,
                    cwd=cwd,
                    exit_code=None,
                    timed_out=True,
                    elapsed_ms=(time.time() - started) * 1000,
                    stdout=stdout or "",
                    stderr=stderr or "",
                )
        except OSError as exc:
            return CommandResult(
                argv=argv,
                cwd=cwd,
                exit_code=None,
                timed_out=False,
                elapsed_ms=(time.time() - started) * 1000,
                stdout="",
                stderr=str(exc),
            )

    def run_command(
        self, spec: CommandSpec, context: dict[str, str] | None = None
    ) -> CommandResult:
        return self._run_command(spec, context or self._context(""))

    def run_install(self, test_file: str) -> list[CommandResult]:
        context = self._context(test_file)
        results: list[CommandResult] = []
        for command in self.install_commands():
            results.append(self._run_command(command, context))
            if not results[-1].ok:
                break
        return results

    def install_marker_path(self) -> Path:
        return self.repo_root / ".benchmark" / "install_state.json"

    def _install_commands_are_dependency_only(self) -> bool:
        commands = self.install_commands()
        if not commands:
            return True
        for command in commands:
            argv = [item.lower() for item in command.argv]
            if argv[:2] == ["corepack", "enable"]:
                continue
            allowed = (
                argv[:2] in (["npm", "ci"], ["npm", "install"])
                or argv[:3] == ["corepack", "pnpm", "install"]
                or argv[:3] == ["corepack", "yarn", "install"]
                or argv[:2] == ["bundle", "install"]
                or argv[:2] == ["pip", "install"]
                or argv[:4] == ["python", "-m", "pip", "install"]
                or argv[:2] == ["uv", "sync"]
                or argv[:2] == ["composer", "install"]
                or argv[:3] == ["go", "mod", "download"]
                or argv[:2] == ["cargo", "fetch"]
            )
            if not allowed:
                return False
        return True

    def retarget_install_marker(
        self, *, old_prepared_commit: str, new_prepared_commit: str
    ) -> bool:
        """Reuse an install marker after source-only changes with stable manifests."""

        if not self._install_commands_are_dependency_only():
            return False
        if not dependency_manifests_unchanged(
            self.repo_root, old_prepared_commit, new_prepared_commit
        ):
            return False
        marker_path = self.install_marker_path()
        if not marker_path.exists():
            return False
        try:
            existing = json.loads(marker_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        if not isinstance(existing, dict) or existing.get("prepared_commit") != old_prepared_commit:
            return False
        expected = self.install_marker_payload(new_prepared_commit)
        existing_without_commit = {key: value for key, value in existing.items() if key != "prepared_commit"}
        expected_without_commit = {key: value for key, value in expected.items() if key != "prepared_commit"}
        if existing_without_commit != expected_without_commit:
            return False
        marker_path.write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def run_install_once(
        self,
        test_file: str = "",
        marker_payload: dict[str, Any] | None = None,
        force: bool = False,
    ) -> list[CommandResult]:
        marker_path = self.install_marker_path()
        payload = marker_payload or {}
        if not force and marker_path.exists():
            try:
                existing = json.loads(marker_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = None
            if existing == payload:
                return []

        results = self.run_install(test_file)
        if all(result.ok for result in results):
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return results

    def run_compile(self) -> ValidationCommandResult:
        spec = self.profile.validation.compile
        if spec is None and self.profile.validation.infer_compile:
            spec = self._inferred_compile_command()
        if spec is None:
            return ValidationCommandResult(name="compile", status="not_configured")
        result = self._run_command(spec, self._context(""))
        return ValidationCommandResult(
            name="compile",
            status="passed" if result.ok else "failed",
            result=result,
        )

    def _missing_vitest_coverage_provider(self, result: CommandResult) -> bool:
        output = result.stdout + "\n" + result.stderr
        return "Cannot find dependency '@vitest/coverage" in output

    def _vitest_coverage_provider_install_command(self, command: CommandSpec) -> CommandSpec | None:
        argv = command.argv
        if "vitest" not in argv or not any("coverage" in item for item in argv):
            return None
        if "pnpm" in argv:
            install_argv = [
                "corepack",
                "pnpm",
                "add",
                "-D",
                "@vitest/coverage-v8",
                "--ignore-workspace-root-check",
            ]
        elif "yarn" in argv:
            install_argv = ["corepack", "yarn", "add", "-D", "@vitest/coverage-v8"]
        elif "npm" in argv:
            install_argv = ["npm", "install", "-D", "@vitest/coverage-v8"]
        else:
            return None
        return CommandSpec(
            argv=install_argv,
            cwd=command.cwd,
            env=command.env,
            timeout_ms=300000,
        )

    def _run_test_command_with_coverage_retry(
        self, command: CommandSpec, context: dict[str, str]
    ) -> list[CommandResult]:
        self._clear_coverage_artifacts()
        result = self._run_command(command, context)
        results = [result]
        if result.ok or not self._missing_vitest_coverage_provider(result):
            return results
        install_command = self._vitest_coverage_provider_install_command(command)
        if install_command is None:
            return results
        install_result = self._run_command(install_command, context)
        results.append(install_result)
        if install_result.ok:
            self._clear_coverage_artifacts()
            results.append(self._run_command(command, context))
        return results

    def run_test_file(self, test_file: str, install: bool = False) -> TestRunResult:
        runners = [runner for runner in self.profile.runners if runner.matches(test_file)]
        context = self._context(test_file)
        auto_commands = [] if runners else self._auto_test_commands(test_file, context)
        if not runners and not auto_commands:
            return TestRunResult(
                repo=self.profile.repo,
                test_file=test_file,
                runner=None,
                status="no_matching_runner",
            )

        install_results = self.run_install(test_file) if install else []
        if install_results and not all(result.ok for result in install_results):
            fallback_runner = runners[0].name if runners else auto_commands[0][0]
            return TestRunResult(
                repo=self.profile.repo,
                test_file=test_file,
                runner=fallback_runner,
                status="install_failed",
                install_results=install_results,
            )

        runner_name = None
        result = None
        attempts = []
        if runners:
            for runner in runners:
                command = CommandSpec(
                    argv=runner.argv,
                    cwd=runner.cwd,
                    env=runner.env,
                    timeout_ms=runner.timeout_ms,
                )
                runner_name = runner.name
                command_results = self._run_test_command_with_coverage_retry(command, context)
                attempts.extend(command_results)
                result = command_results[-1]
                if result.ok:
                    break
        else:
            for name, command in auto_commands:
                runner_name = name
                command_results = self._run_test_command_with_coverage_retry(command, context)
                attempts.extend(command_results)
                result = command_results[-1]
                if result.ok:
                    break
        if result is None:
            return TestRunResult(
                repo=self.profile.repo,
                test_file=test_file,
                runner=None,
                status="no_matching_runner",
                install_results=install_results,
            )
        return TestRunResult(
            repo=self.profile.repo,
            test_file=test_file,
            runner=runner_name,
            status="passed" if result.ok else "failed",
            install_results=install_results,
            test_result=result,
            attempts=attempts,
        )

    def write_result(self, result: TestRunResult) -> Path:
        safe_name = result.test_file.replace("/", "__").replace("\\", "__")
        path = self.output_dir / f"{safe_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path
