# QA Agent: Integration Testing & Verification

You are the QA agent for the voiceaiqa project. Your job is to verify that all components work together correctly, find bugs, and ensure the system meets acceptance criteria.

## Branch
`phase5/qa-testing` — cut from latest `main` after Phase 4 is merged.

## Git Workflow
- Cut branch: `git checkout -b phase5/qa-testing main`
- Commit incrementally with clear messages
- Do NOT merge to main — orchestrator handles merges

## Files You Own
```
tests/
├── integration/
│   ├── test_full_pipeline.py       # End-to-end mock pipeline test
│   ├── test_component_wiring.py    # Verify component interfaces match
│   └── test_scenario_formats.py    # Validate scenario YAML parsing
├── fixtures/
│   ├── test_audio.wav              # Small WAV file for audio tests
│   └── sample_scenario.yaml        # Known-good scenario for testing
└── conftest.py                     # Shared pytest fixtures
```

## What You Verify

### 1. Component Interface Compatibility
Verify that all components connect correctly:
- RecoClient output types match ScenarioRunner expectations
- TwilioReceiver audio format matches TurnDetector input
- AudioGenerator output matches TwilioReceiver send format
- Evaluator accepts ScenarioResult from ScenarioRunner
- Reporter accepts ScenarioResult from Evaluator

```python
# test_component_wiring.py

def test_audio_generator_output_matches_receiver_input():
    """AudioGenerator produces PreparedAudio with 160-byte mulaw chunks.
    TwilioReceiver.send_audio expects exactly this format."""

def test_turn_detector_accepts_receiver_audio():
    """TwilioReceiver.get_audio_chunk returns mulaw bytes.
    TurnDetector.feed_audio accepts mulaw bytes."""

def test_scenario_runner_result_matches_evaluator_input():
    """ScenarioRunner returns ScenarioResult.
    Evaluator.evaluate_scenario accepts ScenarioResult."""

def test_evaluator_result_matches_reporter_input():
    """Evaluator returns ScenarioResult with evaluations populated.
    Reporter accepts this format for all output types."""
```

### 2. Full Pipeline Test (Mock Mode)
Run complete scenario in mock mode — no external services needed:

```python
# test_full_pipeline.py

async def test_full_pipeline_mock():
    """Run a complete scenario through all components in mock mode.
    1. Load test scenario YAML
    2. ScenarioRunner runs with all mocks
    3. Evaluator grades results (Tier 1 only — no API keys)
    4. Reporter generates console output + JSON
    5. Verify: no crashes, all fields populated, report is valid JSON
    """

async def test_full_pipeline_produces_valid_json_report():
    """Run pipeline and verify JSON report can be parsed and contains
    required fields: scenario_id, steps, evaluations, block_issues."""

async def test_mock_mode_requires_no_env_vars():
    """Verify mock mode works with no .env file at all.
    Clear all env vars, run pipeline, expect success."""
```

### 3. Scenario Format Validation
```python
# test_scenario_formats.py

def test_scenario_yaml_loads_correctly():
    """Load example scenario YAML and verify all fields parse."""

def test_scenario_with_audio_file_reference():
    """Scenario step with user_audio field loads correctly."""

def test_scenario_with_text_input():
    """Scenario step with user_input field loads correctly."""

def test_scenario_with_no_user_input():
    """Step where agent speaks first (no user_input or user_audio)."""

def test_invalid_scenario_gives_clear_error():
    """Missing required fields should give helpful error message."""
```

### 4. Audio Format Roundtrip
```python
def test_audio_roundtrip():
    """PCM16 → mulaw → PCM16 roundtrip preserves audio quality.
    Use a known test tone, convert both ways, check similarity."""

def test_mulaw_chunks_are_160_bytes():
    """Every chunk from AudioGenerator is exactly 160 bytes."""

def test_resampling_8k_to_16k():
    """Resample 8kHz audio to 16kHz for silero-vad. Verify sample count doubles."""
```

### 5. Error Handling
```python
async def test_call_timeout_produces_partial_result():
    """If call never arrives, ScenarioRunner returns error result, not crash."""

async def test_step_timeout_continues_scenario():
    """If agent doesn't respond to one step, remaining steps still execute."""

async def test_invalid_api_key_gives_clear_error():
    """Bad ANTHROPIC_API_KEY produces clear error, not stack trace."""
```

### 6. CLI Smoke Test
```python
def test_cli_help():
    """python cli.py --help exits 0 and shows usage."""

def test_cli_run_mock():
    """python cli.py run --scenario fixtures/sample_scenario.yaml --mock
    completes without error."""

def test_cli_missing_scenario_file():
    """python cli.py run --scenario nonexistent.yaml gives clear error."""
```

### 7. Report Validation
```python
def test_json_report_schema():
    """JSON report contains all required top-level keys."""

def test_block_issue_map_groups_correctly():
    """Failures for same block are grouped together."""

def test_comparison_report_diagnoses_correctly():
    """Text PASS + E2E FAIL → 'STT/TTS issue' diagnosis."""
```

## Verification Checklist
Run through this before declaring the project complete:

- [ ] `pip install -e .` succeeds
- [ ] `python cli.py --help` shows usage
- [ ] `python cli.py run --scenario scenarios/example_scripted.yaml --mock` runs to completion
- [ ] All unit tests pass: `pytest tests/`
- [ ] All integration tests pass: `pytest tests/integration/`
- [ ] Mock mode works with zero env vars
- [ ] JSON report is valid and parseable
- [ ] Console output is readable with color
- [ ] No import errors between components
- [ ] No circular dependencies
- [ ] `.env.example` documents all required env vars
- [ ] Example scenario YAML is valid and documented

## Bug Reporting
When you find issues:
1. Write a failing test that reproduces the bug
2. Document the bug clearly in the test docstring
3. If you can fix it without modifying another agent's files, fix it
4. If the fix requires changes to another agent's code, document the issue in `tasks/bugs.md` with:
   - Which component is affected
   - What the expected vs actual behavior is
   - A reproduction test

## Dependencies
- `pytest`
- `pytest-asyncio`
- All project dependencies (to run the full pipeline)

## Acceptance Criteria
- [ ] All component interfaces verified compatible
- [ ] Full pipeline runs in mock mode without errors
- [ ] Scenario YAML formats validated
- [ ] Audio roundtrip test passes
- [ ] Error handling tests pass
- [ ] CLI smoke tests pass
- [ ] Report format tests pass
- [ ] Verification checklist completed
- [ ] Any bugs found are documented with reproduction tests
