# voiceaiqa

Automated QA tool for Japanese outbound voice agents (reco). Receives real phone calls via Twilio, plays scripted audio responses, and evaluates agent behavior across 12 metrics in 3 tiers.

## What It Does

- Triggers reco's outbound agent to call a Twilio number
- Plays scripted audio responses (TTS or pre-recorded WAV)
- Records agent behavior with VAD-based turn detection
- Evaluates across 3 tiers: algorithmic (free), Claude LLM grading, Whisper audio analysis
- Maps failures to specific flow blocks so your team knows exactly what to fix
- Generates JSON reports with block-level issue mapping and severity

## Quick Start

```bash
git clone https://github.com/TiernanGeary/aivoiceqa.git
cd aivoiceqa
pip install -e ".[dev]"
echo "RECO_MOCK_MODE=true" > .env

# Run a test scenario in mock mode (no external services needed)
python cli.py run --scenario scenarios/example_scripted.yaml --mock

# Run the test suite (242 tests)
pytest tests/
```

## How It Works

```
1. CLI loads scenario YAML (scripted conversation steps)
2. AudioGenerator pre-generates all user audio (Cartesia TTS or WAV files)
3. ScenarioRunner triggers reco outbound call → reco dials our Twilio number
4. Twilio webhook opens WebSocket Media Stream
5. For each step:
   - VAD detects when agent finishes speaking (1500ms silence)
   - QA plays prepared audio response
   - Timing + audio recorded
6. After call: fetch transcript from reco API
7. Evaluator grades: Tier 1 (algorithmic) → Tier 2 (Claude) → Tier 3 (Whisper)
8. Reporter outputs console summary + JSON report + block issue map
```

## Evaluation Metrics

### Tier 1: Algorithmic (Free, Instant)
| Metric | What It Checks |
|--------|---------------|
| `call_completed` | Did the call finish without error? |
| `response_latency` | Agent response time (P50/P95) |
| `repetition_detected` | Stuck loops, STT re-asks, or clarifications |
| `silence_or_dead_air` | Gaps > 5s in conversation |
| `turn_count_deviation` | Actual vs expected turn count |

### Tier 2: Claude LLM (~$0.05-0.15/call)
| Metric | What It Checks |
|--------|---------------|
| `block_transition_correct` | Is the agent in the expected flow block? |
| `factual_accuracy` | Does the response match the flow block? |
| `keigo_level_correct` | Appropriate politeness level? |
| `conversation_natural` | Does it sound natural in Japanese? |
| `hallucination_detected` | Did the agent make things up? |
| `must_contain_meaning` | Required semantic content present? |

### Tier 3: Audio Analysis (~$0.02/call)
| Metric | What It Checks |
|--------|---------------|
| `tts_pronunciation` | Spoken audio vs intended text (CER) |
| `stt_accuracy` | Agent response coherent given our input? |

## Writing Scenarios

```yaml
scenario_id: booking_happy_path
mode: scripted
expected_turns:
  min: 3
  max: 8
vad:
  silence_threshold_ms: 1500

steps:
  - step: 1
    # Agent speaks first (outbound call)
    expected_block: greeting
    checks:
      factual: "Agent should greet and identify themselves"
      keigo_level: teineigo

  - step: 2
    user_input: "はい、予約をお願いします"
    expected_block: ask_date
    checks:
      factual: "Agent should ask for preferred date and time"
      must_contain_meaning:
        - "日時"

  - step: 3
    user_audio: "audio/confirm_tuesday.wav"           # Pre-recorded WAV
    user_input: "来週の火曜日、午後2時でお願いします"      # Text ref for eval
    expected_block: confirm_date
    checks:
      factual: "Agent should confirm Tuesday at 2pm"
```

## Reports

JSON reports saved to `reports/` with block-level issue mapping:

```json
{
  "confirm_booking": {
    "issues": [
      {"metric": "factual_accuracy", "severity": "critical", "step": 3},
      {"metric": "hallucination_detected", "severity": "critical", "step": 3}
    ],
    "pass_rate": 0.5
  }
}
```

Severity: **critical** (block wrong, hallucination, stuck loop) | **warning** (keigo, missing meaning) | **info** (naturalness)

## Setting Up for Real Calls

Mock mode works with zero external services. For real calls, you need:

| Service | What For | When Needed |
|---------|----------|-------------|
| Reco API (`localhost:3010`) | Trigger calls, fetch transcripts | Real calls |
| Twilio (JP phone number) | Receive inbound calls | Real calls |
| ngrok | Webhook tunnel to EC2 | Real calls (MVP) |
| Cartesia TTS | Generate Japanese audio | Real calls |
| Anthropic API (Claude) | Tier 2 evaluation | LLM grading |
| OpenAI API (Whisper) | Tier 3 audio analysis | Audio eval |

See [docs/setup_and_usage.md](docs/setup_and_usage.md) for step-by-step setup instructions.

## Configuration

All config via `.env` (see `.env.example`):

```bash
# Minimum for mock mode
RECO_MOCK_MODE=true

# For real calls
RECO_API_URL=http://localhost:3010
RECO_API_TOKEN=<bearer-token>
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+81...
CARTESIA_API_KEY=...
CARTESIA_VOICE_ID=<japanese-voice-id>

# For evaluation
ANTHROPIC_API_KEY=sk-ant-...    # Tier 2
OPENAI_API_KEY=sk-...           # Tier 3 (optional)
```

## Project Structure

```
voiceaiqa/
├── cli.py                 # CLI entry point
├── server.py              # FastAPI server (Twilio webhooks)
├── config/settings.py     # Configuration (from .env)
├── models/                # TestScenario, StepResult, EvalResult
├── reco/client.py         # Reco API client + mock mode
├── receivers/             # Twilio + mock call receivers
├── core/
│   ├── scenario_runner.py # Orchestrates a full test
│   ├── vad.py             # Voice activity detection (silero-vad)
│   ├── audio_gen.py       # Cartesia TTS + audio pipeline
│   ├── evaluator.py       # 3-tier evaluation engine
│   └── reporter.py        # Console + JSON + block issue reports
├── scenarios/             # Test scenario YAML files
├── reports/               # Generated JSON reports
└── tests/                 # 242 tests (unit + integration)
```

## Cost per Test Call (~3 min)

| Component | Cost |
|-----------|------|
| Tier 1 (algorithmic) | $0.00 |
| Tier 2 (Claude, ~5 steps) | ~$0.05-0.15 |
| Tier 3 (Whisper) | ~$0.02 |
| Twilio (inbound JP) | ~$0.10 |
| **Total** | **~$0.17-0.27** |

## Documentation

- [Full Setup & Usage Guide](docs/setup_and_usage.md) — detailed walkthrough
- [Evaluation Metrics Grid](docs/evaluation_metrics.md) — shareable with team
- [Reco DB Change Spec](docs/reco_block_id_change.md) — for adding `final_block_id` later

## License

Internal tool. Not licensed for external use.
