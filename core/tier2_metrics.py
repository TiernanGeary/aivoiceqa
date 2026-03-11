"""Tier 2: Claude LLM-based evaluation metrics.

Uses a single Claude API call per step to evaluate all Tier 2 metrics at once.
"""

from __future__ import annotations

import json
import re
from typing import Any

from config import settings
from models.result import EvalResult, StepResult
from models.scenario import TestStep


class Tier2Evaluator:
    """Evaluates agent responses using Claude Sonnet for nuanced grading."""

    def __init__(self, anthropic_client: Any):
        self._client = anthropic_client
        self._model = settings.EVAL_MODEL
        self._temperature = settings.EVAL_TEMPERATURE

    async def evaluate_step(
        self,
        step: TestStep,
        step_result: StepResult,
        flow_context: str,
        transcript_so_far: str,
    ) -> dict[str, EvalResult]:
        """Evaluate a single step against all Tier 2 metrics.

        Makes ONE Claude API call per step with structured JSON output.
        Returns dict mapping metric name -> EvalResult.
        """
        agent_response = step_result.agent_response_text or ""
        if not agent_response.strip():
            # No response to evaluate — mark all as failed
            return self._empty_results("No agent response to evaluate")

        prompt = self._build_evaluation_prompt(
            step, step_result, flow_context, transcript_so_far
        )

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            temperature=self._temperature,
            system="You are an expert QA evaluator for Japanese voice agents. "
            "You evaluate agent responses for correctness, politeness, and naturalness. "
            "Always respond with valid JSON only — no markdown fences, no explanation.",
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text
        parsed = self._parse_response(raw_text)
        return self._build_eval_results(parsed)

    async def infer_block_from_response(
        self,
        response_text: str,
        flow_context: str,
        expected_block: str,
    ) -> tuple[str, bool]:
        """Use Claude to infer which block the agent is in.

        Returns (inferred_block_id, matches_expected).
        MVP approach — replace with final_block_id from DB when available.
        """
        prompt = (
            f"Given this flow definition:\n{flow_context}\n\n"
            f"The agent said: \"{response_text}\"\n\n"
            f"Which block is the agent most likely in? "
            f"Return ONLY the block ID as plain text, nothing else."
        )

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=128,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        inferred = response.content[0].text.strip()
        return inferred, inferred == expected_block

    def _build_evaluation_prompt(
        self,
        step: TestStep,
        step_result: StepResult,
        flow_context: str,
        transcript: str,
    ) -> str:
        """Build the evaluation prompt for all Tier 2 metrics."""
        checks = step.checks
        must_contain = ", ".join(checks.must_contain_meaning) if checks.must_contain_meaning else "none specified"
        keigo = checks.keigo_level or "not specified"
        factual = checks.factual or "not specified"
        expected_block = step.expected_block or "not specified"
        agent_response = step_result.agent_response_text or ""

        return f"""You are evaluating a Japanese voice agent's response.

## Flow Block Context
{flow_context}

## Expected Behavior
- Expected block: {expected_block}
- Factual requirement: {factual}
- Keigo level: {keigo}
- Must contain meanings: {must_contain}

## Conversation So Far
{transcript}

## Agent's Response (this turn)
{agent_response}

Evaluate and return ONLY this JSON (no markdown, no extra text):
{{
  "block_transition_correct": {{"pass": true/false, "inferred_block": "string", "reasoning": "string"}},
  "factual_accuracy": {{"pass": true/false, "reasoning": "string"}},
  "keigo_level_correct": {{"pass": true/false, "detected_level": "string", "reasoning": "string"}},
  "conversation_natural": {{"pass": true/false, "score": 1-5, "reasoning": "string"}},
  "hallucination_detected": {{"pass": true/false, "hallucinated_content": null, "reasoning": "string"}},
  "must_contain_meaning": {{"pass": true/false, "missing_meanings": [], "reasoning": "string"}}
}}

Rules:
- "pass" means the agent did well on that metric
- For hallucination_detected, pass=true means NO hallucination was found (good)
- For conversation_natural, score is 1 (terrible) to 5 (perfect)
- Be strict on factual accuracy and keigo"""

    def _parse_response(self, raw_text: str) -> dict:
        """Parse Claude's JSON response, handling potential markdown fences."""
        text = raw_text.strip()
        # Strip markdown code fences if present
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            text = match.group(1)
        return json.loads(text)

    def _build_eval_results(self, parsed: dict) -> dict[str, EvalResult]:
        """Convert parsed JSON into EvalResult objects."""
        results: dict[str, EvalResult] = {}

        metric_map = {
            "block_transition_correct": "block_transition_correct",
            "factual_accuracy": "factual_accuracy",
            "keigo_level_correct": "keigo_level_correct",
            "conversation_natural": "conversation_natural",
            "hallucination_detected": "hallucination_detected",
            "must_contain_meaning": "must_contain_meaning",
        }

        for json_key, metric_name in metric_map.items():
            data = parsed.get(json_key, {})
            passed = bool(data.get("pass", False))

            # Normalise score to 0-1 range
            score: float | None = None
            if "score" in data:
                score = float(data["score"]) / 5.0  # 1-5 -> 0.2-1.0

            # Copy all extra fields into details
            details = {k: v for k, v in data.items() if k not in ("pass", "reasoning")}

            results[metric_name] = EvalResult(
                metric=metric_name,
                passed=passed,
                score=score,
                reasoning=data.get("reasoning"),
                details=details if details else None,
            )

        return results

    def _empty_results(self, reason: str) -> dict[str, EvalResult]:
        """Return failed results for all metrics when evaluation cannot proceed."""
        metrics = [
            "block_transition_correct",
            "factual_accuracy",
            "keigo_level_correct",
            "conversation_natural",
            "hallucination_detected",
            "must_contain_meaning",
        ]
        return {
            m: EvalResult(metric=m, passed=False, reasoning=reason)
            for m in metrics
        }
