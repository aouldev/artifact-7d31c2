"""
Path resolution utilities for benchmark data access.

Supports both development (git repo) and artifact (packaged) layouts.
"""

import os
from pathlib import Path


def get_repo_root() -> Path:
    """
    Find the repository root by walking up from this file.

    In development: artifact_staging/benchmark/participant/core/paths.py
    -> repo root is 4 levels up

    In artifact: benchmark/participant/core/paths.py
    -> repo root is 3 levels up
    """
    current = Path(__file__).resolve()

    # Try development layout first (artifact_staging/)
    candidate = current.parents[3]  # artifact_staging/benchmark/participant/core -> artifact_staging
    if (candidate / "benchmark").exists() and (candidate / "data").exists():
        return candidate

    # Fall back to artifact layout
    candidate = current.parents[2]  # benchmark/participant/core -> repo root
    if (candidate / "benchmark").exists() and (candidate / "data").exists():
        return candidate

    # If neither works, use the 3-level default
    return current.parents[2]


def get_dataset_root(release_id: str = "benchmark_release") -> Path:
    """
    Get the release-builder root for a specific dataset release.

    Args:
        release_id: Dataset release identifier (e.g., "benchmark_release")

    Returns:
        Path to data/datasets/{release_id}/. Public participant inputs use
        :func:`get_participant_dataset_root` instead.

    Environment variables:
        DATASET_ROOT: Override the entire dataset root path
    """
    if "DATASET_ROOT" in os.environ:
        return Path(os.environ["DATASET_ROOT"])

    return get_repo_root() / "data" / "datasets" / release_id


def get_participant_dataset_root(release_id: str = "benchmark_release") -> Path:
    """Return the canonical public participant dataset directory.

    Public participant inputs are staged directly under ``data/<release_id>``;
    hidden server data produced by the release builders remains under
    ``data/datasets``.
    """
    if "DATASET_ROOT" in os.environ:
        return Path(os.environ["DATASET_ROOT"])

    return get_repo_root() / "data" / release_id


def get_experiments_pipeline_path() -> Path:
    """Return the staged dataset-construction pipeline directory."""
    return get_repo_root() / "experiments" / "pipeline"


def get_experiments_shims_path() -> Path:
    """Return the public evaluator runner-shim directory."""
    return get_repo_root() / "experiments" / "configs" / "shims"


def get_shims_path() -> Path:
    """Backward-compatible alias for the optional runner-shim directory."""
    return get_experiments_shims_path()


def get_coverage_parser_path() -> Path:
    """Return the staged coverage parser used by the server evaluator."""
    return get_experiments_pipeline_path() / "coverage_parser.mjs"


def get_public_data_path(release_id: str = "benchmark_release") -> Path:
    """
    Get the canonical directory containing public train/validation files.

    Returns:
        Path to data/{release_id}/
    """
    return get_participant_dataset_root(release_id)


def get_private_data_path(release_id: str = "benchmark_release") -> Path:
    """
    Get the directory containing private gold-label files.

    Returns:
        Path to data/datasets/{release_id}/private/
    """
    return get_dataset_root(release_id) / "private"


def get_metadata_path(release_id: str = "benchmark_release") -> Path:
    """
    Get the directory containing dataset metadata files.

    Returns:
        Path to data/datasets/{release_id}/metadata/
    """
    return get_dataset_root(release_id) / "metadata"


def get_manifest_path(release_id: str = "benchmark_release") -> Path:
    """
    Get the path to the dataset manifest.json file.

    Returns:
        Path to data/datasets/{release_id}/manifest.json
    """
    return get_dataset_root(release_id) / "manifest.json"
