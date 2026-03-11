"""Main Evaluator — runs all enabled evaluation tiers on scenario results."""

from __future__ import annotations

from typing import Any

from config import settings
from core.tier1_metrics import run_all_tier1
from core.tier2_metrics import Tier2Evaluator
from core.tier3_metrics import Tier3Evaluator
from models.result import EvalResult, ScenarioResult
from models.scenario import TestScenario, TestStep


class Evaluator:
    """Orchestrates evaluation across all three tiers.

    Args:
        tier1_enabled: Run algorithmic checks (free, instant).
        tier2_enabled: Run Claude LLM-based evaluation.
        tier3_enabled: Run audio-specific evaluation (requires audio data).
        anthropic_client: An anthropic.AsyncAnthropic instance (for Tier 2).
        openai_client: An openai.AsyncOpenAI instance (for Tier 3).
    """

    def __init__(
        self,
        tier1_enabled: bool = True,
        tier2_enabled: bool = True,
        tier3_enabled: bool = False,
        anthropic_client: Any | None = None,
        openai_client: Any | None = None,
    ):
        self.tier1_enabled = tier1_enabled
        self.tier2_enabled = tier2_enabled
        self.tier3_enabled = tier3_enabled

        self._tier2: Tier2Evaluator | None = None
        if tier2_enabled and anthropic_client is not None:
            self._tier2 = Tier2Evaluator(anthropic_client)

        self._tier3: Tier3Evaluator | None = None
        if tier3_enabled and openai_client is not None:
            self._tier3 = Tier3Evaluator(openai_client)

    async def evaluate_scenario(
        self,
        scenario: TestScenario,
        result: ScenarioResult,
        flow_yaml: str | None = None,
    ) -> ScenarioResult:
        """Run all enabled evaluation tiers on a scenario result.

        Mutates result in place (populates evaluations dict on each StepResult).
        Also sets overall pass/fail.

        Returns: The same ScenarioResult with evaluations populated.
        """
        # --- Tier 1: scenario-level metrics ---
        if self.tier1_enabled:
            tier1_results = run_all_tier1(result, scenario)
            # Attach scenario-level metrics to every step's evaluations
            # (or just to the result object — we store them on step 0 if it exists)
            if result.steps:
                result.steps[0].evaluations.update(tier1_results)

        # --- Tier 2 + 3: per-step metrics ---
        transcript_so_far = ""
        for i, step_result in enumerate(result.steps):
            step: TestStep | None = None
            if i < len(scenario.steps):
                step = scenario.steps[i]

            step_evals = await self._evaluate_step(
                step=step,
                step_result=step_result,
                flow_yaml=flow_yaml or "",
                transcript=transcript_so_far,
            )
            step_result.evaluations.update(step_evals)

            # Build running transcript
            user_part = step_result.user_input_text or ""
            agent_part = step_result.agent_response_text or ""
            if user_part:
                transcript_so_far += f"User: {user_part}\n"
            if agent_part:
                transcript_so_far += f"Agent: {agent_part}\n"

        # --- Set per-step pass/fail ---
        for step_result in result.steps:
            if step_result.evaluations:
                step_result.passed = all(
                    e.passed for e in step_result.evaluations.values()
                )
            else:
                step_result.passed = True  # No evals means nothing failed

        # --- Overall pass/fail ---
        result.overall_passed = all(
            s.passed for s in result.steps if s.passed is not None
        )

        return result

    async def _evaluate_step(
        self,
        step: TestStep | None,
        step_result: "StepResult",
        flow_yaml: str,
        transcript: str,
    ) -> dict[str, EvalResult]:
        """Run Tier 2 and Tier 3 on a single step."""
        evals: dict[str, EvalResult] = {}

        # Tier 2: LLM grading
        if self.tier2_enabled and self._tier2 is not None and step is not None:
            tier2_results = await self._tier2.evaluate_step(
                step=step,
                step_result=step_result,
                flow_context=flow_yaml,
                transcript_so_far=transcript,
            )
            evals.update(tier2_results)

        # Tier 3: Audio analysis
        if self.tier3_enabled and self._tier3 is not None:
            if step_result.agent_audio is not None:
                intended = step_result.agent_response_text or ""
                if intended:
                    tts_result = await self._tier3.evaluate_tts_pronunciation(
                        intended_text=intended,
                        audio_bytes=step_result.agent_audio,
                    )
                    evals[tts_result.metric] = tts_result

            if step_result.user_input_text and step_result.agent_response_text:
                stt_result = await self._tier3.evaluate_stt_accuracy(
                    our_text=step_result.user_input_text,
                    agent_response=step_result.agent_response_text,
                )
                evals[stt_result.metric] = stt_result

        return evals
