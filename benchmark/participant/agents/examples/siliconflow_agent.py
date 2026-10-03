"""Simple SiliconFlow/OpenAI-compatible benchmark agent.

Configuration is read from environment variables:

- SILICONFLOW_API_KEY: required
- SILICONFLOW_BASE_URL: default https://api.siliconflow.cn/v1
- SILICONFLOW_MODEL: default Qwen/Qwen2.5-7B-Instruct
- SILICONFLOW_TEMPERATURE: default 0
- SILICONFLOW_MAX_TOKENS: default 512
- SILICONFLOW_TIMEOUT_SECONDS: default 60
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from benchmark.participant.core.base_agent import BaseAgent
from benchmark.participant.core.context import AgentContext
from benchmark.participant.core.models import VALID_MAINTENANCE_LABELS, AgentResult, Episode

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
MAX_DIFF_CHARS = 16_000
MAX_FILE_CHARS = 6_000
MAX_EXAMPLES = 4


class SiliconFlowAgent(BaseAgent):
    """Minimal LLM agent using SiliconFlow's OpenAI-compatible chat API."""

    name = "siliconflow_agent"

    def setup(self, context: AgentContext) -> None:
        self.api_key = os.environ.get("SILICONFLOW_API_KEY")
        if not self.api_key:
            raise RuntimeError("SILICONFLOW_API_KEY is required")
        self.base_url = os.environ.get("SILICONFLOW_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.model = os.environ.get("SILICONFLOW_MODEL", DEFAULT_MODEL)
        self.temperature = float(os.environ.get("SILICONFLOW_TEMPERATURE", "0"))
        self.max_tokens = int(os.environ.get("SILICONFLOW_MAX_TOKENS", "512"))
        self.timeout_seconds = float(os.environ.get("SILICONFLOW_TIMEOUT_SECONDS", "60"))
        self.train_examples = list(context.train_reader.iter())
        context.logger.info(
            "siliconflow setup: model=%s base_url=%s train=%s",
            self.model,
            self.base_url,
            len(self.train_examples),
        )

    def predict(self, episode: Episode, context: AgentContext) -> AgentResult:
        context.tools.repo_info()
        prompt = self._build_prompt(episode, context)
        content = self._chat(prompt)
        parsed = self._parse_json(content)
        label = str(parsed.get("maintenance_label") or "negative").strip().lower()
        if label not in VALID_MAINTENANCE_LABELS:
            label = "negative"
        test_patch = str(parsed.get("test_patch") or "")
        if label == "negative":
            test_patch = ""
        rationale = str(parsed.get("rationale") or content)[:2000]
        return AgentResult(
            maintenance_label=label,  # type: ignore[arg-type]
            test_patch=test_patch,
            rationale=rationale,
            metadata={"model": self.model, "provider": "siliconflow"},
        )

    def _build_prompt(self, episode: Episode, context: AgentContext) -> str:
        examples = self._few_shot_examples(episode.repo)
        file_context = self._file_context(episode, context)
        return "\n\n".join(
            [
                "You are solving a static repo-level test-maintenance benchmark.",
                "Task: Given a production diff and base_commit repository context, predict whether tests need maintenance.",
                "Labels: positive = tests need maintenance; negative = no test maintenance needed.",
                "Return JSON only with keys: maintenance_label, rationale, test_patch.",
                "If maintenance_label is negative, test_patch must be an empty string.",
                "If you are unsure, prefer negative unless the production behavior/API contract clearly changed in a way tests should cover.",
                self._format_examples(examples),
                self._format_episode(episode),
                file_context,
                "JSON answer:",
            ]
        )

    def _few_shot_examples(self, repo: str) -> list[Episode]:
        same_repo = [
            episode for episode in self.train_examples if episode.repo == repo and episode.gold
        ]
        positives = [
            episode
            for episode in same_repo
            if episode.gold and episode.gold.maintenance_label == "positive"
        ]
        negatives = [
            episode
            for episode in same_repo
            if episode.gold and episode.gold.maintenance_label == "negative"
        ]
        selected = positives[:2] + negatives[:2]
        if selected:
            return selected[:MAX_EXAMPLES]
        any_positive = [
            episode
            for episode in self.train_examples
            if episode.gold and episode.gold.maintenance_label == "positive"
        ]
        any_negative = [
            episode
            for episode in self.train_examples
            if episode.gold and episode.gold.maintenance_label == "negative"
        ]
        return (any_positive[:2] + any_negative[:2])[:MAX_EXAMPLES]

    def _format_examples(self, examples: list[Episode]) -> str:
        if not examples:
            return "Training examples: none selected."
        blocks = ["Training examples:"]
        for example in examples:
            assert example.gold is not None
            blocks.append(
                "\n".join(
                    [
                        f"- repo: {example.repo}",
                        f"  commit_message: {example.commit_message}",
                        f"  prod_files: {example.prod_files[:5]}",
                        f"  prod_diff_excerpt: {self._truncate(example.prod_diff, 1200)}",
                        f"  gold_label: {example.gold.maintenance_label}",
                        f"  gold_test_files: {example.gold.test_files[:5]}",
                    ]
                )
            )
        return "\n".join(blocks)

    def _format_episode(self, episode: Episode) -> str:
        return "\n".join(
            [
                "Validation episode:",
                f"episode_id: {episode.episode_id}",
                f"repo: {episode.repo}",
                f"base_commit: {episode.base_commit}",
                f"commit_message: {episode.commit_message}",
                f"prod_files: {episode.prod_files[:20]}",
                f"prod_diff:\n{self._truncate(episode.prod_diff, MAX_DIFF_CHARS)}",
            ]
        )

    def _file_context(self, episode: Episode, context: AgentContext) -> str:
        blocks = ["Base commit file context:"]
        for path in episode.prod_files[:2]:
            try:
                result = context.tools.read_file(path, start=1, end=120)
                content = self._truncate(str(result.get("content") or ""), MAX_FILE_CHARS)
                blocks.append(f"--- {path} at base_commit ---\n{content}")
            except Exception as exc:  # noqa: BLE001 - context is best-effort.
                blocks.append(f"--- {path} at base_commit ---\n<unavailable: {exc}>")
        return "\n".join(blocks)

    def _chat(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful software testing assistant. Return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    data = json.loads(response.read().decode("utf-8"))
                return str(data["choices"][0]["message"]["content"])
            except urllib.error.HTTPError as exc:
                last_error = RuntimeError(
                    f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}"
                )
            except Exception as exc:  # noqa: BLE001 - retry transient API failures.
                last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
        raise RuntimeError(f"SiliconFlow chat request failed: {last_error}")

    def _parse_json(self, content: str) -> dict[str, Any]:
        try:
            data = json.loads(content)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(content[start : end + 1])
                    return data if isinstance(data, dict) else {}
                except json.JSONDecodeError:
                    return {}
            return {}

    def _truncate(self, value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + "\n<truncated>"
