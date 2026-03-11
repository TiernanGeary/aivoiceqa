# voiceaiqa — Setup & Usage Guide

Automated QA tool for Japanese outbound voice agents (reco). Receives real phone calls via Twilio, plays scripted audio responses, and evaluates agent behavior across 12 metrics.

---

## Table of Contents

1. [Quick Start (Mock Mode)](#1-quick-start-mock-mode)
2. [Project Structure](#2-project-structure)
3. [Configuration Reference](#3-configuration-reference)
4. [Writing Scenarios](#4-writing-scenarios)
5. [Running Tests](#5-running-tests)
6. [Setting Up for Real Calls](#6-setting-up-for-real-calls)
7. [Evaluation Metrics](#7-evaluation-metrics)
8. [Reports & Output](#8-reports--output)
9. [Architecture](#9-architecture)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Quick Start (Mock Mode)

Mock mode runs the entire pipeline without external services — no Twilio, no reco, no API keys.

```bash
# Clone and install
git clone https://github.com/TiernanGeary/aivoiceqa.git
cd aivoiceqa
pip install -e ".[dev]"

# Create minimal .env
echo "RECO_MOCK_MODE=true" > .env

# Run a test scenario
python cli.py run --scenario scenarios/example_scripted.yaml --mock

# Run all tests
pytest tests/
```

Expected output:
```
Running scenario: booking_happy_path

============================================================
Scenario: booking_happy_path
Mode: scripted
Duration: 1.3s
Steps completed: 4
  Step 1: OK | agent_audio=379ms | latency=n/a
  Step 2: OK | agent_audio=379ms | latency=n/a
  Step 3: OK | agent_audio=378ms | latency=-232ms
  Step 4: OK | agent_audio=378ms | latency=-233ms
============================================================
```

---

## 2. Project Structure

```
voiceaiqa/
├── cli.py                    # CLI entry point
├── server.py                 # FastAPI server (Twilio webhooks)
├── config/
│   └── settings.py           # All configuration (loaded from .env)
├── models/
│   ├── scenario.py           # TestScenario, TestStep (YAML schema)
│   └── result.py             # StepResult, ScenarioResult, EvalResult
├── reco/
│   ├── client.py             # RecoClient (API + mock mode)
│   └── mock_data.py          # Mock responses for development
├── receivers/
│   ├── base.py               # CallReceiver ABC + ActiveCall
│   ├── twilio_receiver.py    # Twilio Media Streams implementation
│   └── mock_receiver.py      # Mock receiver for testing
├── core/
│   ├── scenario_runner.py    # ScenarioRunner (orchestrates a test)
│   ├── vad.py                # TurnDetector (voice activity detection)
│   ├── audio_utils.py        # mulaw/PCM conversion, resampling
│   ├── audio_gen.py          # AudioGenerator (Cartesia TTS + file loading)
│   ├── audio_cache.py        # SHA256-based audio cache
│   ├── evaluator.py          # Main Evaluator (runs all tiers)
│   ├── tier1_metrics.py      # Algorithmic checks (free)
│   ├── tier2_metrics.py      # Claude LLM grading
│   ├── tier3_metrics.py      # Whisper audio analysis
│   └── reporter.py           # Console + JSON + block issue reports
├── scenarios/
│   └── example_scripted.yaml # Example booking scenario
├── reports/                  # Generated JSON reports
├── cache/audio/              # Cached TTS audio files
├── tests/
│   ├── test_models.py
│   ├── test_reco_client.py
│   ├── test_twilio_receiver.py
│   ├── test_vad.py
│   ├── test_audio_gen.py
│   ├── test_evaluator.py
│   ├── test_reporter.py
│   ├── test_scenario_runner.py
│   └── integration/
│       ├── test_full_pipeline.py
│       ├── test_component_wiring.py
│       └── test_scenario_formats.py
├── .env.example              # All env vars documented
├── pyproject.toml            # Dependencies and project config
└── docs/
    ├── setup_and_usage.md    # This file
    ├── evaluation_metrics.md # Metrics grid (shareable with team)
    └── reco_block_id_change.md # Spec for adding final_block_id to reco
```

---

## 3. Configuration Reference

All config is loaded from `.env` via `config/settings.py`. Copy `.env.example` to `.env` and fill in what you need.

### Mock Mode (development)

| Variable | Default | Description |
|----------|---------|-------------|
| `RECO_MOCK_MODE` | `false` | Skip all external API calls, use synthetic data |

### Reco Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `RECO_API_URL` | `http://localhost:3010` | Reco API base URL |
| `RECO_API_TOKEN` | (empty) | Bearer token for reco API auth |

### Twilio

| Variable | Default | Description |
|----------|---------|-------------|
| `TWILIO_ACCOUNT_SID` | (empty) | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | (empty) | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | (empty) | QA Twilio number (e.g., `+8150...`) |
| `QA_SERVER_PORT` | `8050` | Port for the FastAPI webhook server |

### Audio / TTS

| Variable | Default | Description |
|----------|---------|-------------|
| `CARTESIA_API_KEY` | (empty) | Cartesia TTS API key |
| `CARTESIA_VOICE_ID` | (empty) | Japanese voice ID from Cartesia |
| `AUDIO_CACHE_DIR` | `cache/audio` | Directory for cached TTS audio |

### Evaluation

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (empty) | For Tier 2 Claude evaluation |
| `EVAL_MODEL` | `claude-sonnet-4-20250514` | Claude model for evaluation |
| `EVAL_TEMPERATURE` | `0` | Temperature for eval (0 = deterministic) |
| `OPENAI_API_KEY` | (empty) | For Tier 3 Whisper transcription |

### Thresholds & Timeouts

| Variable | Default | Description |
|----------|---------|-------------|
| `LATENCY_P95_THRESHOLD_MS` | `3000` | P95 latency limit before flagging |
| `DEAD_AIR_THRESHOLD_MS` | `5000` | Silence gap limit before flagging |
| `SCENARIO_STEP_TIMEOUT` | `60` | Max seconds to wait per step |
| `CALL_WAIT_TIMEOUT` | `30` | Max seconds to wait for inbound call |
| `REPORT_OUTPUT_DIR` | `reports` | Where JSON reports are saved |

---

## 4. Writing Scenarios

Scenarios are YAML files in `scenarios/`. Each defines a scripted conversation to test.

### Full Schema

```yaml
scenario_id: booking_happy_path        # Unique identifier (required)
mode: scripted                          # "scripted" (MVP) or "persona" (future)
description: "Basic booking flow test"  # Optional human-readable description
flow_path: flow/flow.yaml              # Optional path to reco flow YAML (for eval context)

expected_turns:
  min: 3                               # Minimum expected turns (default: 1)
  max: 8                               # Maximum expected turns (default: 50)

expected_duration:
  min_seconds: 30                      # Minimum expected call duration (default: 0)
  max_seconds: 180                     # Maximum expected call duration (default: 600)

vad:
  silence_threshold_ms: 1500           # Silence before turn ends (default: 1500)

steps:
  - step: 1
    # Step 1: Agent speaks first (outbound call greeting)
    # No user_input or user_audio — we just listen
    expected_block: greeting            # Which reco flow block we expect
    checks:
      factual: "Agent should greet and identify themselves"
      keigo_level: teineigo             # teineigo | sonkeigo | kenjougo

  - step: 2
    # Step 2: We respond with auto-generated TTS
    user_input: "はい、予約をお願いします"    # Text → Cartesia TTS (or mock sine wave)
    expected_block: ask_date
    checks:
      factual: "Agent should ask for preferred date and time"
      must_contain_meaning:
        - "日時"                        # Agent response must include this concept

  - step: 3
    # Step 3: We respond with a pre-recorded WAV file
    user_audio: "audio/confirm_tuesday.wav"     # Path to WAV file (takes priority over user_input)
    user_input: "来週の火曜日、午後2時でお願いします"  # Kept as text reference for evaluation
    expected_block: confirm_date
    checks:
      factual: "Agent should confirm Tuesday at 2pm"
      must_contain_meaning:
        - "火曜日"
        - "午後2時"
```

### Step Types

| Type | Fields Set | What Happens |
|------|-----------|--------------|
| Agent speaks first | Neither `user_input` nor `user_audio` | QA listens, records agent response |
| Text → TTS | `user_input` only | Auto-generates audio via Cartesia (or mock sine wave) |
| Pre-recorded audio | `user_audio` (+ optional `user_input` as text ref) | Plays WAV file, uses `user_input` text for evaluation |

### Checks Reference

| Field | Type | Description |
|-------|------|-------------|
| `factual` | string | Natural language description of what agent should say (Tier 2 Claude evaluates) |
| `keigo_level` | string | Expected politeness level: `teineigo` (polite), `sonkeigo` (honorific), `kenjougo` (humble) |
| `must_contain_meaning` | list[string] | Keywords or concepts the agent response must include |

### Tips for Writing Scenarios

- **Step 1 should have no `user_input`** — reco makes outbound calls, so the agent speaks first
- **Use `user_audio` for realism** — real audio tests the full STT pipeline
- **Keep `user_input` as text reference** even with `user_audio` — evaluation uses it to compare
- **Match `expected_block` to your reco flow YAML** — this is how block transition accuracy is measured
- **Be specific in `factual` checks** — "Agent should ask for date and time" is better than "Agent responds"

---

## 5. Running Tests

### CLI Commands

```bash
# Run a single scenario (mock mode)
python cli.py run --scenario scenarios/example_scripted.yaml --mock

# Run all scenarios in a directory
python cli.py run --scenario-dir scenarios/ --mock

# Verbose output (debug logging)
python cli.py run --scenario scenarios/example_scripted.yaml --mock -v

# Show help
python cli.py --help
```

### Running the Test Suite

```bash
# All tests (242 total)
pytest tests/

# Specific component
pytest tests/test_vad.py -v
pytest tests/test_evaluator.py -v

# Integration tests only
pytest tests/integration/ -v

# With coverage (if installed)
pytest tests/ --cov=core --cov=reco --cov=receivers
```

---

## 6. Setting Up for Real Calls

Real calls require multiple external services. Set them up in this order.

### Step 1: Reco API Access

Your QA tool runs on the **same EC2 instance** as reco, accessing it via localhost.

```bash
# Add to .env
RECO_MOCK_MODE=false
RECO_API_URL=http://localhost:3010
RECO_API_TOKEN=<your-bearer-token>
```

**How to get the token**: Check your reco API configuration or ask your team. The token is used in the `Authorization: Bearer <token>` header.

### Step 2: Twilio Setup

Twilio receives the inbound call from reco and streams audio to our QA server.

1. **Create a Twilio account** at [twilio.com](https://www.twilio.com) (if you don't have one)

2. **Buy a Japanese phone number**:
   - Twilio Console → Phone Numbers → Buy a Number
   - Country: Japan (+81)
   - Voice capability required

3. **Get your credentials**:
   - Twilio Console → Account → API keys & tokens
   - Copy Account SID and Auth Token

4. **Add to `.env`**:
   ```bash
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_PHONE_NUMBER=+81...
   ```

### Step 3: ngrok Tunnel (MVP Webhook Access)

Twilio needs to reach your QA server's webhook. Since EC2 is behind a security group, use ngrok.

```bash
# Install ngrok
brew install ngrok          # macOS
# or: snap install ngrok    # Ubuntu on EC2

# Start tunnel to your QA server port
ngrok http 8050
```

ngrok gives you a public URL like `https://abc123.ngrok-free.app`. You need this for the Twilio webhook.

### Step 4: Configure Twilio Webhook

1. Twilio Console → Phone Numbers → Active Numbers → select your JP number
2. Voice & Fax → "A CALL COMES IN"
3. Set to **Webhook** → `https://<your-ngrok-url>/incoming`
4. Method: HTTP POST
5. Save

### Step 5: Cartesia TTS

Cartesia generates the Japanese audio we play to the agent.

1. Sign up at [cartesia.ai](https://cartesia.ai)
2. Get an API key
3. Browse available Japanese voices and copy a voice ID

```bash
# Add to .env
CARTESIA_API_KEY=...
CARTESIA_VOICE_ID=<japanese-voice-id>
```

### Step 6: Start the QA Server

```bash
# Terminal 1: Start the webhook server
python -m uvicorn server:app --host 0.0.0.0 --port 8050

# Terminal 2: Start ngrok (if not already running)
ngrok http 8050

# Terminal 3: Run a test
python cli.py run --scenario scenarios/example_scripted.yaml
```

**What happens:**
1. CLI triggers reco via `POST localhost:3010/api/calls/start`
2. Reco's agent dials your Twilio number
3. Twilio hits your ngrok webhook → WebSocket Media Stream opens
4. QA plays audio, records agent responses, runs evaluation
5. Results printed to console + saved as JSON report

### Step 7: Evaluation API Keys (Optional for MVP)

**Tier 2 — Claude evaluation** (recommended):
```bash
ANTHROPIC_API_KEY=sk-ant-...
```
Gives you: factual accuracy, block transition, keigo, hallucination detection, must-contain-meaning checks.

**Tier 3 — Whisper transcription** (optional):
```bash
OPENAI_API_KEY=sk-...
```
Gives you: TTS pronunciation accuracy (compare what reco intended vs what was actually spoken).

Without these keys, only Tier 1 algorithmic metrics run (latency, repetition, turn count, dead air, call completion).

---

## 7. Evaluation Metrics

### Tier 1: Algorithmic (Free, Instant)

Always runs. No API keys needed.

| Metric | What It Checks | Pass Condition |
|--------|---------------|----------------|
| `call_completed` | Did the call finish without error? | No error in result |
| `response_latency` | How fast does the agent respond? | P95 < 3000ms |
| `repetition_detected` | Is the agent repeating itself? | No stuck loops or STT re-asks |
| `silence_or_dead_air` | Any long gaps in conversation? | No gaps > 5000ms |
| `turn_count_deviation` | Did the call have the expected number of turns? | Within min-max range |

**Repetition Classification:**

| Type | Pattern | Root Cause | Severity |
|------|---------|------------|----------|
| `stuck_loop` | 3+ near-identical responses | Flow logic bug (infinite loop) | Critical |
| `stt_reask` | Agent asks to repeat (すみません、もう一度...) | STT/audio quality issue | Critical |
| `clarification` | Different clarifying questions | Normal behavior | Info (pass) |

### Tier 2: Claude LLM (Requires `ANTHROPIC_API_KEY`)

Runs per step. One Claude API call per step (~$0.003-0.01 per step).

| Metric | What It Checks |
|--------|---------------|
| `block_transition_correct` | Is the agent in the expected flow block? (Inferred from response content) |
| `factual_accuracy` | Does the response match what the flow block prescribes? |
| `keigo_level_correct` | Is the politeness level appropriate for the context? |
| `conversation_natural` | Does the exchange sound natural in Japanese? (Scored 1-5) |
| `hallucination_detected` | Did the agent say anything not in the flow? |
| `must_contain_meaning` | Does the response include required semantic content? |

### Tier 3: Audio Analysis (Requires `OPENAI_API_KEY`)

Runs when agent audio is available. Uses Whisper for transcription.

| Metric | What It Checks |
|--------|---------------|
| `tts_pronunciation` | Does the spoken audio match the intended text? (Character Error Rate) |
| `stt_accuracy` | Does the agent's response make sense given what we said? |

### Cost per Test Call (~3 min, 5 steps)

| Component | Cost |
|-----------|------|
| Tier 1 (algorithmic) | $0.00 |
| Tier 2 (Claude Sonnet, ~5 calls) | ~$0.05-0.15 |
| Tier 3 (Whisper, ~5 min audio) | ~$0.02 |
| Twilio (inbound JP, 3 min) | ~$0.10 |
| **Total per call** | **~$0.17-0.27** |

---

## 8. Reports & Output

### Console Output

The CLI prints a summary after each scenario:

```
============================================================
Scenario: booking_happy_path
Mode: scripted
Duration: 45.2s
Steps: 4/5 passed
Latency P50: 1.2s | P95: 2.1s
Overall: FAIL
============================================================
```

With `-v` (verbose), you also get per-step details with metrics tables.

### JSON Reports

Saved automatically to `reports/{scenario_id}_{timestamp}.json`:

```json
{
  "scenario_id": "booking_happy_path",
  "test_mode": "scripted",
  "timestamp": "2026-03-10T14:30:00",
  "duration_s": 45.2,
  "overall_passed": false,
  "summary": {
    "total_steps": 5,
    "passed_steps": 4,
    "latency_p50_ms": 1200,
    "latency_p95_ms": 2100
  },
  "block_issues": {
    "confirm_booking": {
      "block_id": "confirm_booking",
      "issues": [
        {
          "metric": "factual_accuracy",
          "severity": "critical",
          "detail": "Agent confirmed wrong date",
          "step": 3
        }
      ],
      "pass_rate": 0.5
    }
  },
  "steps": [
    {
      "step": 1,
      "user_input": null,
      "agent_response": "お電話ありがとうございます...",
      "expected_block": "greeting",
      "latency_ms": 1200,
      "evaluations": {
        "factual_accuracy": {"passed": true, "reasoning": "..."},
        "keigo_level_correct": {"passed": true, "reasoning": "..."}
      }
    }
  ]
}
```

### Block Issue Map

The most actionable output — tells your team exactly which flow blocks have problems:

```json
{
  "confirm_booking": {
    "block_id": "confirm_booking",
    "issues": [
      {"metric": "factual_accuracy", "severity": "critical", "step": 3},
      {"metric": "hallucination_detected", "severity": "critical", "step": 3}
    ],
    "pass_rate": 0.5
  },
  "ask_date": {
    "block_id": "ask_date",
    "issues": [
      {"metric": "keigo_level_correct", "severity": "warning", "step": 2}
    ],
    "pass_rate": 0.75
  }
}
```

**Severity levels:**
- **critical**: Block transition wrong, hallucination, stuck loop — needs immediate fix
- **warning**: Keigo incorrect, missing meaning, high latency — should fix
- **info**: Minor naturalness issues, clarification requests — nice to fix

---

## 9. Architecture

### How a Test Works (End-to-End)

```
1. CLI loads scenario YAML → TestScenario
2. AudioGenerator pre-generates all step audio (TTS or file loading)
3. ScenarioRunner triggers reco: POST localhost:3010/api/calls/start
4. Reco's agent dials our Twilio number
5. Twilio webhook hits /incoming → returns TwiML → WebSocket opens
6. For each step:
   a. TurnDetector listens for agent speech via VAD
   b. Agent finishes speaking (1500ms silence)
   c. ScenarioRunner plays our prepared audio
   d. Timing recorded (latency = our audio end → agent speech start)
7. After final step: hang up
8. Fetch transcript + metadata from reco API
9. Evaluator runs Tier 1 (instant) → Tier 2 (Claude) → Tier 3 (Whisper)
10. Reporter generates console output + JSON report
```

### Deployment (EC2)

```
EC2 Instance (Tokyo, ap-northeast-1)
├── reco-rta (Asterisk + orchestrator, port 3010)
├── voiceaiqa (FastAPI, port 8050)
│   ├── Talks to reco API via localhost:3010
│   ├── Receives Twilio webhooks via ngrok (MVP)
│   └── Runs evaluation after calls
├── nginx (routes traffic)
└── PostgreSQL → AWS RDS
```

### Audio Format Pipeline

```
Cartesia TTS → PCM16 24kHz
                ↓ resample
            PCM16 8kHz
                ↓ encode
            mulaw 8kHz
                ↓ chunk
        160-byte frames (20ms each)
                ↓ base64
        Twilio Media Stream JSON
```

---

## 10. Troubleshooting

### Mock mode won't start
- Check `.env` exists with `RECO_MOCK_MODE=true`
- Run `pip install -e ".[dev]"` to ensure dependencies are installed

### Tests failing
```bash
# Run with verbose to see which test fails
pytest tests/ -v

# Run a specific test file
pytest tests/test_vad.py -v
```

### "audioop is deprecated" warning
This warning appears on Python 3.12 and the module is removed in 3.13. The VAD engine (`core/audio_utils.py`) has a fallback, but `core/audio_gen.py` still uses audioop. If you're on Python 3.13+, this needs to be updated.

### Twilio webhook not working
1. Check ngrok is running: `curl https://<your-url>/incoming` should return XML
2. Check Twilio console → Phone Number → webhook URL matches your ngrok URL
3. Check QA server is running: `curl http://localhost:8050/incoming`
4. Check ngrok dashboard at `http://127.0.0.1:4040` for request logs

### Call timeout (call never arrives)
- Verify reco is running on localhost:3010
- Verify `RECO_API_TOKEN` is correct
- Check reco logs for the outbound call attempt
- Verify the Twilio phone number is correct in your scenario or reco config

### No Tier 2 evaluation results
- Verify `ANTHROPIC_API_KEY` is set in `.env`
- Tier 2 is skipped if the key is missing — check CLI output for "Tier 2 skipped" message

### Latency numbers seem wrong
- Negative latency in mock mode is expected (synthetic timing)
- Real latency is measured from when our audio finishes playing to when the agent starts speaking
- P95 > 3000ms flags as a warning by default (configurable via `LATENCY_P95_THRESHOLD_MS`)

### How to add a new scenario
1. Copy `scenarios/example_scripted.yaml`
2. Change `scenario_id` to something unique
3. Update `steps` to match your reco flow
4. Set `expected_block` for each step to match your flow block IDs
5. Add `checks` for what you want to evaluate
6. Run: `python cli.py run --scenario scenarios/your_new_scenario.yaml --mock`
