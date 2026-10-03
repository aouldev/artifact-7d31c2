from __future__ import annotations

import pytest

from benchmark.participant.core.runner import _validate_validation_rows


def test_validation_rows_reject_gold_leakage() -> None:
    with pytest.raises(ValueError, match="forbidden fields"):
        _validate_validation_rows([{"episode_id": "e1", "gold": {"maintenance_label": "positive"}}])


def test_validation_rows_reject_metadata_leakage() -> None:
    with pytest.raises(ValueError, match="metadata fields"):
        _validate_validation_rows([{"episode_id": "e1", "metadata": {"source": "mixed_commit"}}])
