from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from benchmark.server.tools.dynamic_runner import DynamicTestRunner
from benchmark.server.tools.repo_profile import load_repo_profile, render_argv, render_template


def test_render_argv_replaces_profile_context() -> None:
    assert render_argv(
        ["pnpm", "vitest", "{{test_file}}", "--out", "{{output_dir}}"],
        {"test_file": "src/foo.test.ts", "output_dir": "/tmp/out"},
    ) == ["pnpm", "vitest", "src/foo.test.ts", "--out", "/tmp/out"]


def test_load_repo_profile_and_select_runner(tmp_path: Path) -> None:
    profile_path = tmp_path / "repo.json"
    profile_path.write_text(
        json.dumps(
            {
                "repo": "demo",
                "env": {"CI": "1"},
                "runners": [
                    {
                        "name": "unit",
                        "path_regex": ".*\\.test\\.py$",
                        "argv": [sys.executable, "-c", "print('ok')"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    profile = load_repo_profile(profile_path)

    assert profile.repo == "demo"
    assert profile.select_runner("tests/test_demo.test.py") is not None
    assert profile.select_runner("README.md") is None


def test_dynamic_runner_executes_matching_command(tmp_path: Path) -> None:
    profile_path = tmp_path / "repo.json"
    profile_path.write_text(
        json.dumps(
            {
                "repo": "demo",
                "runners": [
                    {
                        "name": "python_smoke",
                        "path_regex": ".*\\.py$",
                        "cwd": "{{repo_root}}",
                        "argv": [sys.executable, "-c", "print('{{test_file}}')"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = tmp_path / "out"

    runner = DynamicTestRunner(load_repo_profile(profile_path), repo_root, output_dir)
    result = runner.run_test_file("tests/test_demo.py")
    result_path = runner.write_result(result)

    assert result.status == "passed"
    assert result.test_result is not None
    assert "tests/test_demo.py" in result.test_result.stdout
    assert result_path.exists()


def test_dynamic_runner_infers_install_and_compile(tmp_path: Path) -> None:
    profile_path = tmp_path / "repo.json"
    profile_path.write_text(json.dumps({"repo": "demo"}), encoding="utf-8")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "package.json").write_text(
        json.dumps({"scripts": {"typecheck": "node -e \"console.log('typecheck ok')\""}}),
        encoding="utf-8",
    )
    (repo_root / "package-lock.json").write_text("{}", encoding="utf-8")

    runner = DynamicTestRunner(load_repo_profile(profile_path), repo_root, tmp_path / "out")
    install_commands = runner.install_commands()
    compile_result = runner.run_compile()

    assert install_commands[0].argv == ["npm", "ci"]
    assert compile_result.status == "passed"
    assert compile_result.result is not None
    assert "typecheck ok" in compile_result.result.stdout


def test_dynamic_runner_auto_fallback_commands_use_nearest_package(tmp_path: Path) -> None:
    profile_path = tmp_path / "repo.json"
    profile_path.write_text(
        json.dumps({"repo": "demo", "validation": {"infer_compile": False}}),
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    package_dir = repo_root / "packages" / "unit"
    test_dir = package_dir / "src"
    test_dir.mkdir(parents=True)
    (repo_root / "package.json").write_text(
        json.dumps({"packageManager": "npm@10.0.0"}), encoding="utf-8"
    )
    (repo_root / "package-lock.json").write_text("{}", encoding="utf-8")
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "scripts": {},
                "devDependencies": {},
            }
        ),
        encoding="utf-8",
    )
    runner = DynamicTestRunner(load_repo_profile(profile_path), repo_root, tmp_path / "out")
    context = runner.context("packages/unit/src/example.test.ts")
    commands = runner._auto_test_commands("packages/unit/src/example.test.ts", context)

    assert commands[0][0] == "auto_vitest_1"
    assert commands[0][1].argv[:4] == ["npm", "exec", "--", "vitest"]
    assert commands[0][1].cwd == "packages/unit"
    rendered_argv = render_argv(commands[0][1].argv, context)
    rendered_cwd = render_template(commands[0][1].cwd or "{{repo_root}}", context)
    assert "vitest" in rendered_argv
    assert "packages/unit/src/example.test.ts" in rendered_argv
    assert rendered_cwd == "packages/unit"


def test_dynamic_runner_explicit_runners_fall_back_in_order(tmp_path: Path) -> None:
    profile_path = tmp_path / "repo.json"
    profile_path.write_text(
        json.dumps(
            {
                "repo": "demo",
                "runners": [
                    {
                        "name": "first",
                        "path_regex": ".*\\.test\\.py$",
                        "cwd": "{{repo_root}}",
                        "argv": [sys.executable, "-c", "import sys; sys.exit(1)"],
                    },
                    {
                        "name": "second",
                        "path_regex": ".*\\.test\\.py$",
                        "cwd": "{{repo_root}}",
                        "argv": [sys.executable, "-c", "print('fallback ok')"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    runner = DynamicTestRunner(load_repo_profile(profile_path), repo_root, tmp_path / "out")
    result = runner.run_test_file("tests/example.test.py")

    assert result.status == "passed"
    assert result.runner == "second"
    assert len(result.attempts) == 2
    assert result.test_result is not None
    assert "fallback ok" in result.test_result.stdout


def test_dynamic_runner_expands_profile_env_values(tmp_path: Path) -> None:
    profile_path = tmp_path / "repo.json"
    profile_path.write_text(
        json.dumps(
            {
                "repo": "demo",
                "env": {"CUSTOM_PATH": "prefix:${PATH}:{{output_dir}}"},
                "runners": [
                    {
                        "name": "env",
                        "path_regex": ".*\\.py$",
                        "cwd": "{{repo_root}}",
                        "argv": [
                            sys.executable,
                            "-c",
                            "import os; print(os.environ['CUSTOM_PATH'])",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = tmp_path / "out"

    runner = DynamicTestRunner(load_repo_profile(profile_path), repo_root, output_dir)
    result = runner.run_test_file("tests/example.py")

    assert result.status == "passed"
    assert result.test_result is not None
    assert "${PATH}" not in result.test_result.stdout
    assert str(output_dir) in result.test_result.stdout


def test_dynamic_runner_prepares_shim_assets(tmp_path: Path) -> None:
    from benchmark.server.tools.dynamic_runner import DynamicTestRunner
    from benchmark.server.tools.repo_profile import RepoProfile

    repo = tmp_path / "repo"
    repo.mkdir()
    runner = DynamicTestRunner(
        profile=RepoProfile(repo="demo"), repo_root=repo, output_dir=tmp_path / "out"
    )

    assert (runner.output_dir / "playwright_browser_coverage_shim.cjs").exists()
    assert runner.context()["sandboxDir"] == str(runner.output_dir)


@pytest.mark.parametrize(
    "caller_network_env",
    [{}, {"HTTPS_PROXY": "http://proxy.example.invalid:8080", "NO_PROXY": "example.invalid"}],
    ids=["direct", "caller_proxy"],
)
def test_dynamic_runner_preserves_caller_network_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller_network_env: dict[str, str]
) -> None:
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "REPO_TEST_EVOLUTION_PROXY",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in caller_network_env.items():
        monkeypatch.setenv(key, value)

    repo = tmp_path / "repo"
    repo.mkdir()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps({
            "repo": "demo",
            "runners": [{
                "name": "network_environment",
                "argv": [sys.executable, "-c", (
                    "import json, os; print(json.dumps({key: value "
                    "for key, value in os.environ.items() if key.lower() "
                    "in {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}}))"
                )],
            }],
        }),
        encoding="utf-8",
    )
    runner = DynamicTestRunner(load_repo_profile(profile_path), repo, tmp_path / "out")
    result = runner.run_test_file("tests/example.py")

    assert result.status == "passed"
    assert result.test_result is not None
    assert json.loads(result.test_result.stdout) == caller_network_env
