# Agent F: Evaluation Pipeline

You are building the evaluation pipeline for the voiceaiqa QA tool. This component grades agent responses across three tiers of metrics: algorithmic checks, LLM-based evaluation, and audio-specific analysis.

## Branch
`phase4/evaluation` — cut from latest `main` after Phase 3 is merged.

## Git Workflow
- Cut branch: `git checkout -b phase4/evaluation main`
- Commit incrementally with clear messages
- Do NOT merge to main — orchestrator handles merges

## Files You Own
```
core/
├── evaluator.py          # Main Evaluator class — runs all tiers
├── tier1_metrics.py      # Algorithmic / deterministic checks
├── tier2_metrics.py      # Claude LLM-based evaluation
└── tier3_metrics.py      # Audio-specific (Whisper transcription)
```

## What You Build

### Three-Tier Evaluation Architecture

#### Tier 1: Algorithmic (Free, Instant)
Deterministic checks that don't require LLM calls:

| # | Metric | How |
|---|--------|-----|
| 1 | `call_completed` | Did call finish without error? Check call status |
| 2 | `response_latency` | P50/P95 from TurnDetector timestamps. Flag if P95 > threshold |
| 3 | `repetition_detected` | Compare consecutive agent responses. Classify: `stuck_loop`, `stt_reask`, `clarification` |
| 4 | `silence_or_dead_air` | Flag gaps > 5s between turns |
| 5 | `turn_count_deviation` | Compare actual turns vs expected turns from scenario |

```python
# tier1_metrics.py

def check_call_completed(result: ScenarioResult) -> EvalResult:
    """Check if call completed successfully."""

def check_response_latency(result: ScenarioResult, p95_threshold_ms: float = 3000) -> EvalResult:
    """Check P50/P95 latency. Flag if P95 exceeds threshold."""

def check_repetition(steps: list[StepResult]) -> EvalResult:
    """Detect repeated responses. Classify type:
    - stuck_loop: exact/near-exact same response 3+ times
    - stt_reask: agent asks to repeat (すみません、もう一度...)
    - clarification: legitimate clarifying question (different each time)
    Uses simple text similarity (difflib.SequenceMatcher)."""

def check_dead_air(steps: list[StepResult], threshold_ms: float = 5000) -> EvalResult:
    """Flag gaps between turns exceeding threshold."""

def check_turn_count(result: ScenarioResult, scenario: TestScenario) -> EvalResult:
    """Compare actual vs expected number of turns."""
```

#### Tier 2: LLM-Graded (Claude Sonnet)
Uses Claude API for nuanced evaluation:

| # | Metric | What Claude Evaluates |
|---|--------|-----------------------|
| 6 | `block_transition_correct` | Did agent move to the expected block? (Inferred from response content since final_block_id not in DB yet) |
| 7 | `factual_accuracy` | Does response match what the flow block prescribes? |
| 8 | `keigo_level_correct` | Is the politeness level appropriate? (teineigo/sonkeigo/kenjougo) |
| 9 | `conversation_natural` | Does the exchange sound natural in Japanese? |
| 10 | `hallucination_detected` | Did agent say anything not supported by the flow? |
| 11 | `must_contain_meaning` | Does response contain required semantic content? |

```python
# tier2_metrics.py

class Tier2Evaluator:
    def __init__(self, anthropic_client):
        """Uses Claude Sonnet for evaluation."""

    async def evaluate_step(
        self,
        step: TestStep,
        step_result: StepResult,
        flow_context: str,
        transcript_so_far: str,
    ) -> dict[str, EvalResult]:
        """Evaluate a single step against all Tier 2 metrics.
        Makes ONE Claude API call per step with structured JSON output.

        The prompt includes:
        - The flow block definition (expected behavior)
        - The scenario step expectations
        - The agent's actual response (transcript text)
        - Full conversation history so far

        Returns dict mapping metric name → EvalResult.
        """

    async def infer_block_from_response(
        self, response_text: str, flow_context: str, expected_block: str
    ) -> tuple[str, bool]:
        """Use Claude to infer which block the agent is in based on response content.
        Returns (inferred_block_id, matches_expected).
        This is the MVP approach — replace with final_block_id from DB when available."""

    def _build_evaluation_prompt(self, step, step_result, flow_context, transcript) -> str:
        """Build the evaluation prompt. Uses temperature=0 for consistency."""
```

**Evaluation Prompt Design:**
- System prompt establishes Claude as a Japanese language QA expert
- Include flow block definition so Claude knows what the agent SHOULD say
- Request structured JSON output with pass/fail + reasoning for each metric
- Use temperature=0 for deterministic grading
- Single API call per step evaluates all Tier 2 metrics at once

**Example prompt structure:**
```
You are evaluating a Japanese voice agent's response.

## Flow Block Context
{flow_block_yaml}

## Expected Behavior
- Expected block: {expected_block}
- Factual requirement: {step.checks.factual}
- Keigo level: {step.checks.keigo_level}
- Must contain meanings: {step.checks.must_contain_meaning}

## Conversation So Far
{transcript}

## Agent's Response (this turn)
{agent_response}

Evaluate and return JSON:
{
  "block_transition_correct": {"pass": bool, "inferred_block": str, "reasoning": str},
  "factual_accuracy": {"pass": bool, "reasoning": str},
  "keigo_level_correct": {"pass": bool, "detected_level": str, "reasoning": str},
  "conversation_natural": {"pass": bool, "score": 1-5, "reasoning": str},
  "hallucination_detected": {"pass": bool, "hallucinated_content": str|null, "reasoning": str},
  "must_contain_meaning": {"pass": bool, "missing_meanings": list[str], "reasoning": str}
}
```

#### Tier 3: Audio-Specific (Whisper)
Requires recorded audio — only available for E2E voice calls:

| # | Metric | How |
|---|--------|-----------------------|
| 12 | `tts_pronunciation` | Compare reco's intended text (from transcript API) vs Whisper transcription of recorded audio |
| 13 | `stt_accuracy` | Did agent response make sense given what we said? (Indirect — we know what we said) |

```python
# tier3_metrics.py

class Tier3Evaluator:
    def __init__(self, openai_client):
        """Uses OpenAI Whisper for transcription."""

    async def transcribe_audio(self, audio_bytes: bytes, language: str = "ja") -> str:
        """Transcribe audio using Whisper API. Returns text."""

    async def evaluate_tts_pronunciation(
        self, intended_text: str, audio_bytes: bytes
    ) -> EvalResult:
        """Compare what reco intended to say (from transcript API)
        vs what Whisper hears from the recording.
        Uses character error rate (CER) for Japanese."""

    async def evaluate_stt_accuracy(
        self, our_text: str, agent_response: str
    ) -> EvalResult:
        """Indirect STT check: does the agent's response make sense
        given what we said? If we said 'Tuesday 2pm' and agent confirms
        'Wednesday 3pm', STT likely misheard us.
        Uses Claude for semantic comparison."""
```

### Main Evaluator (core/evaluator.py)

```python
class Evaluator:
    def __init__(
        self,
        tier1_enabled: bool = True,
        tier2_enabled: bool = True,
        tier3_enabled: bool = False,  # Requires audio, off by default
    ):
        ...

    async def evaluate_scenario(
        self,
        scenario: TestScenario,
        result: ScenarioResult,
        flow_yaml: str | None = None,
    ) -> ScenarioResult:
        """Run all enabled evaluation tiers on a scenario result.
        Mutates result in place (populates evaluations dict on each StepResult).
        Also sets overall pass/fail.

        Args:
            scenario: The test scenario definition
            result: The raw scenario result (from ScenarioRunner)
            flow_yaml: The flow YAML content (for block inference)

        Returns: The same ScenarioResult with evaluations populated.
        """

    async def _evaluate_step(
        self, step: TestStep, step_result: StepResult, flow_yaml: str, transcript: str
    ) -> dict[str, EvalResult]:
        """Run all tiers on a single step. Returns merged evaluation dict."""
```

### EvalResult Data Class
```python
@dataclass
class EvalResult:
    metric: str
    passed: bool
    score: float | None = None      # 0.0-1.0 for graded metrics
    reasoning: str | None = None    # Why it passed/failed
    details: dict | None = None     # Extra data (e.g., inferred_block, detected_level)
```

Add this to `models/result.py` if not already there.

### Repetition Classification Detail
Three types with different root causes:
- **`stuck_loop`**: Agent repeats exact same response 3+ times. Root cause: flow logic bug (infinite loop in block)
- **`stt_reask`**: Agent asks user to repeat (pattern match: すみません、もう一度、聞き取れません). Root cause: STT/audio issue
- **`clarification`**: Agent asks different clarifying questions. Root cause: none — this is normal behavior

Use `difflib.SequenceMatcher` for similarity. Threshold: >0.85 similarity = same response.

## Configuration
Read from `config/settings.py`:
- `ANTHROPIC_API_KEY` (for Tier 2)
- `OPENAI_API_KEY` (for Tier 3 Whisper)
- `EVAL_MODEL` (default "claude-sonnet-4-20250514")
- `EVAL_TEMPERATURE` (default 0)
- `LATENCY_P95_THRESHOLD_MS` (default 3000)
- `DEAD_AIR_THRESHOLD_MS` (default 5000)

## Dependencies
- `anthropic` for Tier 2 (Claude API)
- `openai` for Tier 3 (Whisper transcription)
- `difflib` (stdlib) for repetition detection
- Shared models from `models/`

## Testing
Write tests in `tests/test_evaluator.py`:
- Test all Tier 1 metrics with synthetic data
- Test repetition classification (stuck_loop, stt_reask, clarification)
- Test Tier 2 prompt construction (verify prompt includes all context)
- Test Tier 2 response parsing (mock Claude response)
- Test EvalResult construction
- Test overall pass/fail logic

## Acceptance Criteria
- [ ] All 5 Tier 1 metrics work with deterministic test data
- [ ] Repetition classified correctly into 3 types
- [ ] Tier 2 evaluator makes single Claude call per step
- [ ] Tier 2 prompt includes flow context, expectations, and conversation history
- [ ] Block inference works via Claude (MVP without final_block_id)
- [ ] Tier 3 Whisper transcription works
- [ ] TTS pronunciation comparison uses CER
- [ ] Overall pass/fail correctly aggregates all metrics
- [ ] All tests pass
