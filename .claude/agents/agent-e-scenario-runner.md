# Agent E: Scenario Runner + CLI

You are building the scenario runner and CLI for the voiceaiqa QA tool. This component wires all Phase 2 components together — it orchestrates a full test scenario from start to finish.

## Branch
`phase3/scenario-runner-cli` — cut from latest `main` after Phase 2 is merged.

## Git Workflow
- Cut branch: `git checkout -b phase3/scenario-runner-cli main`
- Commit incrementally with clear messages
- Do NOT merge to main — orchestrator handles merges

## Files You Own
```
core/
└── scenario_runner.py    # ScenarioRunner — orchestrates a full test scenario
cli.py                    # Minimal CLI entry point
```

## What You Build

### ScenarioRunner class (core/scenario_runner.py)

Wires together: RecoClient, TwilioReceiver, TurnDetector, AudioGenerator, and returns a ScenarioResult.

```python
class ScenarioRunner:
    def __init__(
        self,
        reco_client: RecoClient,
        receiver: CallReceiver,
        turn_detector: TurnDetector,
        audio_generator: AudioGenerator,
    ):
        """All dependencies injected — makes testing easier."""

    async def run_scenario(self, scenario: TestScenario) -> ScenarioResult:
        """Execute a full test scenario. Steps:

        1. Pre-generate all audio for the scenario (audio_generator.prepare_scenario_audio)
        2. Register pending test with receiver (maps phone number → scenario)
        3. Trigger outbound call via reco_client.start_call()
        4. Wait for inbound call on receiver (receiver.wait_for_call)
        5. For each step in scenario:
           a. Wait for agent to finish speaking (turn_detector)
           b. Record agent's audio (turn_detector.get_turn_audio)
           c. Send our prepared audio response (receiver.send_audio)
           d. Mark audio sent timestamp (turn_detector.mark_our_audio_sent)
           e. Record step result with timing data
        6. After last step: wait for agent's final response, then hang up
        7. Poll reco for call completion (reco_client.poll_status)
        8. Fetch transcript and conversation data from reco
        9. Build and return ScenarioResult
        """

    async def _run_step(
        self, step: TestStep, prepared_audio: PreparedAudio, call: ActiveCall
    ) -> StepResult:
        """Execute a single step in the scenario."""

    async def _wait_for_agent_turn(self, call: ActiveCall) -> tuple[bytes, float]:
        """Listen to agent audio via turn_detector until turn ends.
        Returns (agent_audio_mulaw, duration_ms)."""

    async def _play_audio(self, call: ActiveCall, prepared_audio: PreparedAudio) -> None:
        """Send prepared audio chunks to the call at 20ms intervals."""
```

### Main Flow Sequence
```
1. Pre-generate audio for all steps
2. Register pending test (phone → scenario mapping)
3. Trigger reco outbound call → reco calls our Twilio number
4. Twilio webhook hits /incoming → connects Media Stream
5. WebSocket established → TwilioReceiver has ActiveCall

Loop for each step:
  6. Agent speaks (greets or responds)
  7. TurnDetector detects silence → turn ended
  8. Record agent audio + timing
  9. Play our audio response
  10. Mark audio sent timestamp
  11. Record step result

12. After final step: wait for agent's last response
13. Hang up
14. Fetch post-call data from reco API
15. Return ScenarioResult (unevaluated — evaluation is Phase 4)
```

### Error Handling
- **Call timeout**: If no call arrives within 30s, fail with clear error
- **Step timeout**: If agent doesn't respond within 60s, record timeout and continue to next step
- **Call drops**: If WebSocket disconnects mid-scenario, record partial results
- **Audio errors**: If a prepared audio chunk fails to send, log and continue

### StepResult Construction
For each step, build a StepResult with:
- `step_number`: from scenario step
- `agent_audio`: raw mulaw bytes of agent's response
- `agent_audio_duration_ms`: how long the agent spoke
- `expected_block`: from scenario step
- `actual_block`: None for now (populated by evaluator in Phase 4)
- `latency_ms`: from turn_detector.get_latency()
- `evaluations`: empty dict (populated by evaluator in Phase 4)
- `passed`: None (determined after evaluation)

### CLI (cli.py)

Minimal CLI using `argparse` (no Click dependency):

```python
# Usage:
#   python cli.py run --scenario scenarios/booking_happy_path.yaml
#   python cli.py run --scenario-dir scenarios/booking/
#   python cli.py run --scenario scenarios/booking.yaml --mock

def main():
    parser = argparse.ArgumentParser(description="voiceaiqa - Voice Agent QA Tool")
    subparsers = parser.add_subparsers(dest="command")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run test scenario(s)")
    run_parser.add_argument("--scenario", type=str, help="Path to scenario YAML file")
    run_parser.add_argument("--scenario-dir", type=str, help="Directory of scenario YAML files")
    run_parser.add_argument("--mock", action="store_true", help="Use mock mode (no real calls)")

    args = parser.parse_args()
    ...
```

The CLI should:
1. Load settings from `.env`
2. Parse scenario YAML into TestScenario
3. Initialize all components (with mock mode support)
4. Run ScenarioRunner
5. Print basic results summary to console (detailed reporting is Phase 4)

### Mock Mode
When `--mock` is passed:
- RecoClient uses mock mode (no real API calls)
- AudioGenerator uses mock mode (sine wave instead of Cartesia)
- TwilioReceiver is replaced with a MockReceiver that simulates call flow
- TurnDetector processes mock audio

This enables full pipeline testing without any external services.

### MockReceiver (receivers/mock_receiver.py)
A simple CallReceiver implementation for testing:
```python
class MockReceiver(CallReceiver):
    """Simulates receiving a call. Feeds synthetic agent audio
    and captures sent audio. Used for mock mode testing."""
```
Note: This file is owned by Agent E since it's an integration concern.

## Dependencies on Phase 2 Components
- `reco/client.py` — RecoClient (Agent A)
- `receivers/twilio_receiver.py` — TwilioReceiver (Agent B)
- `core/vad.py` — TurnDetector (Agent C)
- `core/audio_gen.py` — AudioGenerator (Agent D)
- `models/scenario.py` — TestScenario, TestStep (Phase 1)
- `models/result.py` — StepResult, ScenarioResult (Phase 1)

## Configuration
Read from `config/settings.py`:
- All settings from Phase 2 components
- `SCENARIO_STEP_TIMEOUT` (default 60s)
- `CALL_WAIT_TIMEOUT` (default 30s)

## Testing
Write tests in `tests/test_scenario_runner.py`:
- Test full scenario run in mock mode (end-to-end with all mocks)
- Test step timeout handling
- Test call timeout handling
- Test partial results on call drop
- Test CLI argument parsing

## Acceptance Criteria
- [ ] ScenarioRunner orchestrates full scenario flow
- [ ] All Phase 2 components integrate correctly
- [ ] Mock mode runs full pipeline without external services
- [ ] CLI can run a single scenario
- [ ] CLI can run a directory of scenarios
- [ ] Error handling works for timeouts and disconnects
- [ ] Step results capture timing data (latency, duration)
- [ ] All tests pass
