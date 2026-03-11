"""Tier 1: Algorithmic / deterministic evaluation metrics.

These are free and instant — no API calls required.
"""

from __future__ import annotations

import difflib
import re
import statistics
from typing import Sequence

from config import settings
from models.result import EvalResult, ScenarioResult, StepResult
from models.scenario import TestScenario


def check_call_completed(result: ScenarioResult) -> EvalResult:
    """Check if the call completed successfully (no scenario-level error)."""
    passed = result.error is None
    reasoning = None if passed else f"Call error: {result.error}"
    return EvalResult(
        metric="call_completed",
        passed=passed,
        score=1.0 if passed else 0.0,
        reasoning=reasoning,
    )


def check_response_latency(
    result: ScenarioResult,
    p95_threshold_ms: float | None = None,
) -> EvalResult:
    """Check P50/P95 latency. Flag if P95 exceeds threshold."""
    if p95_threshold_ms is None:
        p95_threshold_ms = settings.LATENCY_P95_THRESHOLD_MS

    latencies = [
        s.latency_ms for s in result.steps if s.latency_ms is not None
    ]

    if not latencies:
        return EvalResult(
            metric="response_latency",
            passed=True,
            score=None,
            reasoning="No latency data available",
            details={"p50_ms": None, "p95_ms": None},
        )

    latencies_sorted = sorted(latencies)
    p50 = statistics.median(latencies_sorted)

    # P95: index = ceil(0.95 * n) - 1, clamped
    idx_95 = max(0, min(len(latencies_sorted) - 1, int(0.95 * len(latencies_sorted))))
    p95 = latencies_sorted[idx_95]

    passed = p95 <= p95_threshold_ms
    return EvalResult(
        metric="response_latency",
        passed=passed,
        score=max(0.0, 1.0 - (p95 / p95_threshold_ms)) if p95_threshold_ms > 0 else 1.0,
        reasoning=f"P50={p50:.0f}ms, P95={p95:.0f}ms (threshold={p95_threshold_ms:.0f}ms)",
        details={"p50_ms": p50, "p95_ms": p95, "threshold_ms": p95_threshold_ms},
    )


# Patterns that indicate the agent is asking the user to repeat themselves.
_STT_REASK_PATTERNS = re.compile(
    r"(すみません|もう一度|聞き取れません|聞き取れませんでした|もう一回)",
    re.IGNORECASE,
)

# Similarity threshold: above this, two responses are considered "the same".
_SIMILARITY_THRESHOLD = 0.85


def _text_similarity(a: str, b: str) -> float:
    """Return 0-1 similarity ratio between two strings."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def check_repetition(steps: Sequence[StepResult]) -> EvalResult:
    """Detect repeated agent responses.

    Classify type:
    - stuck_loop: same/near-same response 3+ times in a row
    - stt_reask: agent asks to repeat (すみません、もう一度...)
    - clarification: legitimate clarifying questions (different each time)
    """
    responses = [s.agent_response_text or "" for s in steps]

    if len(responses) < 2:
        return EvalResult(
            metric="repetition_detected",
            passed=True,
            score=1.0,
            reasoning="Fewer than 2 responses — no repetition possible",
            details={"type": None},
        )

    # Check for stuck_loop: 3+ consecutive near-identical responses
    max_consecutive = 1
    current_run = 1
    for i in range(1, len(responses)):
        if _text_similarity(responses[i], responses[i - 1]) >= _SIMILARITY_THRESHOLD:
            current_run += 1
            max_consecutive = max(max_consecutive, current_run)
        else:
            current_run = 1

    if max_consecutive >= 3:
        return EvalResult(
            metric="repetition_detected",
            passed=False,
            score=0.0,
            reasoning=f"Agent repeated near-identical response {max_consecutive} times in a row",
            details={"type": "stuck_loop", "max_consecutive": max_consecutive},
        )

    # Check for stt_reask: agent asks user to repeat
    reask_count = sum(1 for r in responses if _STT_REASK_PATTERNS.search(r))
    if reask_count >= 2:
        return EvalResult(
            metric="repetition_detected",
            passed=False,
            score=0.3,
            reasoning=f"Agent asked user to repeat {reask_count} times (possible STT issue)",
            details={"type": "stt_reask", "reask_count": reask_count},
        )

    # Check for repeated pairs (not necessarily consecutive 3+)
    # If there are duplicates but they are all different, it's clarification
    pair_sims = [
        _text_similarity(responses[i], responses[j])
        for i in range(len(responses))
        for j in range(i + 1, len(responses))
        if _text_similarity(responses[i], responses[j]) >= _SIMILARITY_THRESHOLD
    ]
    if len(pair_sims) >= 2:
        return EvalResult(
            metric="repetition_detected",
            passed=True,
            score=0.7,
            reasoning="Some similar responses detected — likely clarification",
            details={"type": "clarification", "similar_pairs": len(pair_sims)},
        )

    return EvalResult(
        metric="repetition_detected",
        passed=True,
        score=1.0,
        reasoning="No repetition detected",
        details={"type": None},
    )


def check_dead_air(
    steps: Sequence[StepResult],
    threshold_ms: float | None = None,
) -> EvalResult:
    """Flag gaps between turns exceeding threshold.

    Uses latency_ms on each step as a proxy for the gap between the user
    finishing speaking and the agent starting to respond.
    """
    if threshold_ms is None:
        threshold_ms = settings.DEAD_AIR_THRESHOLD_MS

    gaps = [s.latency_ms for s in steps if s.latency_ms is not None]

    if not gaps:
        return EvalResult(
            metric="silence_or_dead_air",
            passed=True,
            score=None,
            reasoning="No timing data available",
            details={"flagged_steps": []},
        )

    flagged = [
        {"step": steps[i].step_number, "gap_ms": gaps[i]}
        for i, g in enumerate(gaps)
        if g > threshold_ms
    ]

    passed = len(flagged) == 0
    return EvalResult(
        metric="silence_or_dead_air",
        passed=passed,
        score=1.0 if passed else max(0.0, 1.0 - len(flagged) / len(gaps)),
        reasoning=(
            f"No dead air detected (threshold={threshold_ms:.0f}ms)"
            if passed
            else f"{len(flagged)} gap(s) exceeded {threshold_ms:.0f}ms"
        ),
        details={"flagged_steps": flagged, "threshold_ms": threshold_ms},
    )


def check_turn_count(result: ScenarioResult, scenario: TestScenario) -> EvalResult:
    """Compare actual vs expected number of turns."""
    actual = len(result.steps)
    expected_min = scenario.expected_turns.min
    expected_max = scenario.expected_turns.max

    passed = expected_min <= actual <= expected_max
    return EvalResult(
        metric="turn_count_deviation",
        passed=passed,
        score=1.0 if passed else 0.0,
        reasoning=(
            f"Actual turns={actual}, expected range=[{expected_min}, {expected_max}]"
        ),
        details={
            "actual_turns": actual,
            "expected_min": expected_min,
            "expected_max": expected_max,
        },
    )


def run_all_tier1(
    result: ScenarioResult,
    scenario: TestScenario,
) -> dict[str, EvalResult]:
    """Run all Tier 1 metrics and return a dict of metric_name -> EvalResult."""
    evals: dict[str, EvalResult] = {}

    cc = check_call_completed(result)
    evals[cc.metric] = cc

    rl = check_response_latency(result)
    evals[rl.metric] = rl

    rep = check_repetition(result.steps)
    evals[rep.metric] = rep

    da = check_dead_air(result.steps)
    evals[da.metric] = da

    tc = check_turn_count(result, scenario)
    evals[tc.metric] = tc

    return evals
