"""Example agent that always predicts negative."""

from __future__ import annotations

from benchmark.participant.core.base_agent import BaseAgent
from benchmark.participant.core.context import AgentContext
from benchmark.participant.core.models import Episode


class DummyAgent(BaseAgent):
    name = "dummy_agent"

    def setup(self, context: AgentContext) -> None:
        context.logger.info("dummy setup: train episodes=%s", len(context.train_reader))

    def predict(self, episode: Episode, context: AgentContext) -> dict:
        # Exercise one read-only tool so the trace path is validated.
        context.tools.repo_info()
        return {
            "maintenance_label": "negative",
            "test_patch": "",
            "rationale": "DummyAgent always predicts negative.",
        }
