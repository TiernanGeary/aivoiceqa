"""Result types for scenario runs and evaluations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EvalResult:
    """Result of a single metric evaluation."""
    metric: str
    passed: bool
    score: float | None = None          # 0.0-1.0 for graded metrics
    reasoning: str | None = None        # Why it passed/failed
    details: dict | None = None         # Extra data (inferred_block, detected_level, etc.)


@dataclass
class StepResult:
    """Result of a single step in a scenario run."""
    step_number: int
    user_input_text: str | None = None      # What we said (text reference)
    agent_response_text: str | None = None  # What agent said (transcript)
    agent_audio: bytes | None = None        # Raw mulaw of agent's response
    agent_audio_duration_ms: float = 0.0
    expected_block: str | None = None
    actual_block: str | None = None         # Inferred by evaluator
    latency_ms: float | None = None
    evaluations: dict[str, EvalResult] = field(default_factory=dict)
    passed: bool | None = None              # Set after evaluation
    error: str | None = None                # If step had an error


@dataclass
class ScenarioResult:
    """Result of a complete scenario run."""
    scenario_id: str
    test_mode: str                          # "scripted" or "persona"
    steps: list[StepResult] = field(default_factory=list)
    overall_passed: bool | None = None      # Set after evaluation
    started_at: datetime = field(default_factory=datetime.now)
    duration_s: float = 0.0
    call_id: str | None = None
    conversation_id: int | None = None
    reco_transcript: str | None = None      # Full transcript from reco API
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    error: str | None = None                # If scenario-level error occurred
