from __future__ import annotations

import pytest

from benchmark.participant.core.models import AgentResult, Episode


def test_episode_without_gold_removes_gold() -> None:
    episode = Episode.from_dict(
        {
            "episode_id": "e1",
            "repo": "r",
            "base_commit": "c1",
            "prod_files": ["src/a.ts"],
            "prod_diff": "diff --git ...",
            "gold": {"maintenance_label": "positive", "test_patch": "patch", "test_files": []},
        }
    )

    assert episode.gold is not None
    assert episode.without_gold().gold is None
    assert "gold" not in episode.without_gold().to_dict(include_gold=False)


def test_negative_prediction_must_have_empty_patch() -> None:
    with pytest.raises(ValueError, match="empty test_patch"):
        AgentResult.from_dict({"maintenance_label": "negative", "test_patch": "diff --git ..."})


def test_non_binary_prediction_labels_are_rejected() -> None:
    with pytest.raises(ValueError, match="invalid prediction maintenance_label"):
        AgentResult.from_dict({"maintenance_label": "unsupported"})


def test_non_binary_gold_labels_are_rejected() -> None:
    with pytest.raises(ValueError, match="invalid gold maintenance_label"):
        Episode.from_dict(
            {
                "episode_id": "e1",
                "repo": "r",
                "base_commit": "c1",
                "prod_files": [],
                "prod_diff": "",
                "gold": {"maintenance_label": "unsupported"},
            }
        )
