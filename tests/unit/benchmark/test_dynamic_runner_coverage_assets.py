"""Coverage cleanup must never delete runner preload assets."""

import sys
from pathlib import Path

import pytest

from benchmark.server.evaluators.coverage_ucr import evaluate_update_coverage
from benchmark.server.tools.dynamic_runner import DynamicTestRunner
from benchmark.server.tools.repo_profile import CommandSpec, RepoProfile


@pytest.mark.parametrize("equals_form", [False, True])
def test_vitest_cleanup_preserves_worker_shim(tmp_path: Path, equals_form: bool) -> None:
    runner = DynamicTestRunner(RepoProfile(repo="vendure"), tmp_path, tmp_path / "out")
    shim = runner.output_dir / "vendure_core_dashboard_test_shim.cjs"
    assert shim.is_file()
    original = shim.read_bytes()
    flag = "--coverage.reportsDirectory"
    arguments = [f"{flag}={runner.output_dir}"] if equals_form else [flag, str(runner.output_dir)]
    # Model Vitest's cleanup followed by the worker loading NODE_OPTIONS assets.
    script = (
        "import pathlib, shutil, sys; "
        "p = pathlib.Path(sys.argv[-1].split('=', 1)[-1]); "
        "shutil.rmtree(p, ignore_errors=True); p.mkdir(parents=True); "
        "assert pathlib.Path(sys.argv[1]).is_file(); "
        "(p / 'lcov.info').write_text('TN:\\nSF:calc.js\\nDA:1,1\\nend_of_record\\n')"
    )
    result = runner.run_command(CommandSpec([sys.executable, "-c", script, str(shim), *arguments]))
    assert result.ok, result.stderr
    assert shim.read_bytes() == original
    assert (runner.output_dir / "coverage/vitest/lcov.info").is_file()
    (tmp_path / "calc.js").write_text("export const value = 2;\n")
    coverage = evaluate_update_coverage(
        prod_diff="diff --git a/calc.js b/calc.js\n--- a/calc.js\n+++ b/calc.js\n@@ -1 +1 @@\n-export const value = 1;\n+export const value = 2;\n",
        prod_files=["calc.js"],
        repo_root=tmp_path,
        coverage_root=runner.output_dir,
    )
    assert coverage.ucr_status == "passed", coverage.coverage


def test_explicit_separate_coverage_directory_is_preserved(tmp_path: Path) -> None:
    runner = DynamicTestRunner(RepoProfile(repo="vendure"), tmp_path, tmp_path / "out")
    separate = runner.output_dir / "custom-coverage"
    result = runner.run_command(
        CommandSpec(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[-1])",
                "--coverage.reportsDirectory",
                str(separate),
            ]
        )
    )
    assert result.ok
    assert result.stdout.strip() == str(separate)
