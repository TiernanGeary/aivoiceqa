# Voice Agent QA Automation Tool

## Context
The team manually calls Japanese voice agents to find hallucinations, wrong block transitions, and language issues. The agent (reco) makes **outbound** calls via Asterisk + custom SIP trunk + OpenAI Realtime API + Cartesia TTS. There's no text chat endpoint — conversations happen entirely over audio.

Building a QA tool as an **internal reco testing tool first** (tight integration via reco's existing REST API), can be abstracted for other agents later.

## Key Backend Facts (reco-rta)
- **Code location**: `/Users/tiernangeary/Downloads/realtime-tmp/reco-rta/`
- **Orchestrator**: `reco_rta/realtime.py` — `RealtimeOrchestrator` manages OpenAI Realtime WebSocket
- **Flow engine**: `reco_rta/flow.py` — `FlowEngine` loads YAML flows, tracks current block via `set_block()`
- **Block transitions**: OpenAI calls `update_state(next_block_id, reason)` tool → `flow.set_block()`
- **Transcripts**: `turn_log: list[tuple[str, str]]`, persisted via `realtime_logging.log_call_finish()`
- **Call trigger**: `POST /api/calls/start` with `{phone, customer_id, flow_path}`
- **Outbound via**: Asterisk ARI + custom SIP trunk
- **DB**: PostgreSQL on AWS RDS (Tokyo, `ap-northeast-1`)
- **API**: FastAPI on EC2 (port 3010 behind nginx), bearer token auth, IP-restricted security group
- **Storage**: Transcripts + recordings in AWS S3

## Deployment
QA tool runs on the **same EC2 instance** as reco, accessing the API via `localhost:3010`.

```
EC2 Instance
├── reco-rta (Asterisk + orchestrator)
├── reco API (FastAPI, port 3010)
├── voiceaiqa (FastAPI, port 8050)        ← QA tool
│   ├── Talks to reco API via localhost:3010
│   ├── Receives Twilio webhooks via ngrok (MVP) or nginx (production)
│   └── Runs evaluation after calls
├── nginx (existing, routes traffic)
├── redis
└── PostgreSQL → AWS RDS
```

**Twilio webhook access:**
- **MVP**: ngrok tunnel (`ngrok http 8050`) — zero security group changes
- **Production**: Add nginx route `/qa/` → `localhost:8050`, whitelist Twilio IPs in security group

**To start a test session (MVP):**
```bash
# Terminal 1: Start QA server
cd voiceaiqa && python server.py  # listens on localhost:8050

# Terminal 2: Start ngrok tunnel
ngrok http 8050
# → gives you https://abc123.ngrok.io

# Terminal 3: Run a test
python cli.py run --scenario scenarios/test.yaml --webhook-url https://abc123.ngrok.io
```

## Reco Connection (zero reco changes)
QA tool connects to reco via its **existing REST API** on localhost. No DB access, no reco code changes.

```
RECO_API_URL=http://localhost:3010
RECO_API_TOKEN=your-bearer-token
```

**Reco API endpoints used by QA:**
| Endpoint | Purpose |
|----------|---------|
| `POST /api/calls/start` | Trigger outbound call (returns call_id, conversation_id) |
| `GET /api/calls/status?call_id=...` | Poll call status until completed |
| `GET /conversations/{id}` | Call metadata: final_block_id, final_block_status, call_start_at, duration |
| `GET /conversations/{id}/transcript` | Full transcript (from S3) — what agent intended to say |
| `GET /recordings/{id}/audio` | Agent's recording (presigned S3 URL) — what agent actually said |

## Architecture

### How a test works (end-to-end flow):
```
1. QA server already running on port 8050 (with ngrok tunnel for MVP)
2. QA triggers reco via POST localhost:3010/api/calls/start
   → agent dials QA's Twilio number
3. Twilio receives call → hits ngrok URL → WebSocket Media Stream opens
4. Agent speaks → QA records audio + runs VAD for silence detection
5. VAD detects silence (~1500ms) → QA plays pre-generated TTS audio
6. Repeat steps 4-5 until scenario complete
7. QA hangs up
8. QA polls GET localhost:3010/api/calls/status until completed
9. QA fetches transcript + recording + metadata from reco API (localhost)
10. Tier 1: Algorithmic metrics computed instantly (free)
11. Tier 3: Whisper transcribes agent recording → TTS accuracy check
12. Tier 2: Claude evaluates factual accuracy, instruction following, STT inference
13. Report generated
```

### Two test modes:

**Scripted mode** — QA is a "jukebox": plays audio at the right moments. No AI during the call. All intelligence is post-call evaluation.

Audio input options per step:
- `user_input` (text) → auto-generate TTS via Cartesia
- `user_audio` (file path) → use your own recording (most realistic)
- Both → `user_audio` takes priority, `user_input` kept as text reference for evaluation

```yaml
scenario_id: booking_happy_path
mode: scripted
expected_turns:
  min: 5
  max: 12
expected_duration:
  min_seconds: 60
  max_seconds: 300
vad:
  silence_threshold_ms: 1500
steps:
  - step: 1
    # No user input — agent speaks first (outbound call)
    expected_block: greeting
    checks:
      factual: "Agent should greet and identify themselves"

  - step: 2
    # Option A: auto-generate TTS from text
    user_input: "はい、予約をお願いします"
    expected_block: ask_date
    checks:
      factual: "Agent should ask for preferred date/time"

  - step: 3
    # Option B: use your own recording
    user_audio: "audio/confirm_tuesday_2pm.wav"
    user_input: "来週の火曜日、午後2時でお願いします"  # text reference for evaluation
    expected_block: confirm_date
    checks:
      factual: "Agent should confirm Tuesday at 2pm"
```

**Persona mode** — QA is an AI caller: live Whisper STT → GPT-4o-mini generates response → TTS → play. Evaluation is post-call only.
```yaml
scenario_id: impatient_customer_booking
mode: persona
expected_turns:
  min: 5
  max: 20
expected_duration:
  min_seconds: 60
  max_seconds: 600
vad:
  silence_threshold_ms: 1500
persona:
  description: "忙しいサラリーマン。来週火曜の14時に予約したい。少しイライラしている。"
  objective: "Successfully book an appointment for next Tuesday at 2pm"
  behavior: "Speaks quickly, may interrupt, gives short answers"
  max_turns: 15
evaluation:
  objective_met: "Was the appointment successfully booked?"
  checks:
    - "Agent never hallucinated information"
    - "Agent did not repeat itself"
```

## Evaluation Metrics

### MVP Metrics (12 total)

#### Tier 1: Algorithmic — instant, $0
| # | Metric | How Measured | Mode | Data Source |
|---|--------|-------------|------|-------------|
| 1 | **Block correctness** | `final_block_id == expected terminal block` | Scripted | Reco API |
| 2 | **Turn count** | Count turns, compare to expected range | Both | Reco transcript |
| 3 | **Call duration** | `end - start`, compare to expected range | Both | Reco API metadata |
| 4 | **Latency** | Time delta: our audio stops → agent starts (VAD timestamps) | Both | Live capture |
| 5 | **Repetition detection** | SequenceMatcher >80% between consecutive agent turns. Classified as: `stuck_loop` (no user input/acknowledgment), `stt_reask` (agent couldn't process clear user input), or `clarification` (legitimate re-ask, not a failure). Classification uses Tier 2 Claude eval context. | Both | Reco transcript |
| 6 | **Flow completion** | Check `final_block_status` | Both | Reco API |

#### Tier 2: LLM-graded (Claude Sonnet) — ~$0.06-0.15/call
| # | Metric | How Measured | Mode | Data Source |
|---|--------|-------------|------|-------------|
| 7 | **Factual accuracy** | Claude compares agent response vs flow block content | Both | Reco transcript + flow YAML |
| 8 | **Instruction following** | Claude checks if agent accomplished block goal | Scripted | Reco transcript + flow YAML |
| 9 | **Objective met** | Claude evaluates full transcript holistically | Both | Full transcript + objective |
| 10 | **STT inference** | Claude checks if agent response makes sense given our input | Both | Scenario text + agent response |

**Evaluation strategy by mode:**
- **Scripted**: Parallel Claude calls, one per turn (small context each). + 1 end-of-call. Total: ~N+1 calls.
- **Persona**: One single Claude call at end with full flow YAML + full transcript. Claude infers blocks per-turn and evaluates everything together. Cheaper, avoids sending large flow YAML N times.

#### Tier 3: Audio-specific — ~$0.02/call
| # | Metric | How Measured | Mode | Data Source |
|---|--------|-------------|------|-------------|
| 11 | **TTS accuracy** | Compare reco transcript vs Whisper transcription of recording | Both | Reco transcript + recording → Whisper |
| 12 | **TTS intelligibility** | Whisper confidence scores on agent audio | Both | Whisper on recording |

**STT models:**
- **Live STT** (persona mode, during call): Whisper — speed matters, accuracy "good enough"
- **Evaluation STT** (post-call, Tier 3): Whisper for MVP → Google Cloud Speech-to-Text v2 later (best Japanese accuracy)

### Post-MVP Metrics
| # | Metric | Description |
|---|--------|-------------|
| 13 | **Japanese quality** | Keigo correctness, naturalness, business phone conventions |
| 14 | **Semantic completeness** | `must_contain_meaning` checks with semantic equivalence |

### QA-specific cost per test call (~3 min):
| Component | Cost |
|-----------|------|
| Twilio inbound (Japan ~$0.055/min) | ~$0.17 |
| Claude evaluation (Sonnet) | ~$0.06-0.15 |
| TTS for user audio | ~$0.03 |
| Whisper transcription | ~$0.02 |
| **QA overhead per call** | **~$0.28-0.37** |

## Technical Architecture

### Audio: Twilio Media Streams (bidirectional WebSocket)
```
Reco (agent) ──► Twilio ◄══ WebSocket ══► QA Server (port 8050)
                  │                         │
                  │ audio chunks (mulaw)     │ receives agent audio
                  │ 8kHz, 20ms frames       │ sends user audio back
                  │                         │ real-time VAD
```
- Call arrives → TwiML `<Connect><Stream url="wss://..." /></Connect>`
- Twilio opens bidirectional WebSocket to QA server
- Inbound: JSON `{"event":"media","media":{"payload":"base64-mulaw"}}` every ~20ms
- Outbound: same format, QA sends pre-converted mulaw audio
- Control events: `connected`, `start` (with streamSid), `stop`
- Pre-call: convert all TTS audio from WAV/PCM → mulaw 8kHz

### Turn detection: VAD (silero-vad)
- State machine: IDLE → AGENT_SPEAKING → SILENCE_DETECTED → PLAY_RESPONSE
- **Silence threshold: 1500ms** (conservative for Japanese — no benefit to responding fast on QA side, avoiding false turn-end detection)
- `min_speech_duration_ms: 300` — ignore short sounds (clicks, coughs, static)
- Configurable per scenario via `vad.silence_threshold_ms`
- Latency metric captured as byproduct: `T_agent_speech_start - T_our_audio_end`
- Edge cases:
  - Background noise: filtered by min_speech_duration
  - Agent restarts mid-sentence: reset turn audio buffer, keep accumulating
  - Our audio overlaps agent: send Twilio `clear` event to stop playback

### Scenario runner orchestration:

**Pre-call:**
1. Load scenario YAML
2. Generate TTS audio for all steps (scripted) or init GPT-4o-mini persona caller
3. Convert audio to mulaw 8kHz
4. Register pending test in server memory

**During call (scripted — "jukebox"):**
```
For each turn:
  → VAD waits for agent to speak
  → Agent audio accumulates in buffer
  → 1500ms silence → save turn audio, record latency
  → Play next pre-generated audio file
  → Wait for playback to finish
  → If no more steps: hang up
```

**During call (persona — "AI caller"):**
```
For each turn:
  → VAD waits for agent to speak
  → Agent audio accumulates in buffer
  → 1500ms silence → save turn audio
  → Whisper transcribes agent turn (~1-2s)
  → GPT-4o-mini generates persona response (~1-2s)
  → TTS generates + converts audio (~0.5-1s)
  → Play audio (~3-5s total gap, agent waits)
  → If max_turns reached or GPT says "done": hang up
```

**Post-call:**
1. Poll reco API until call completed (timeout 60s)
2. Fetch: conversation metadata, transcript, recording URL
3. Tier 1 metrics (instant)
4. Tier 3: download recording, Whisper transcribe, compare to reco transcript
5. Tier 2: Claude evaluation (parallel per-turn for scripted, single call for persona)
6. Compile ScenarioResult → generate report

**Timeouts:**
| What | Timeout | On failure |
|------|---------|------------|
| Waiting for call to arrive | 30s | "Agent never called" |
| Agent silence per turn | 15s | "Agent went silent on turn N" |
| Reco API call completion | 60s | "Call didn't complete in reco" |
| Total test duration | 10 min | Force hangup, evaluate what we have |

### Evaluator prompts (Claude Sonnet):

**Per-turn (scripted mode) — one call per turn, parallelized:**
- Input: agent response, block content, block goal, user input, conversation history
- Evaluates: factual_accuracy (hallucinated claims), instruction_following (block goal met), stt_inference (response makes sense given our input)
- Output: JSON with pass/fail, score 0-1, detail per metric
- **Must include `block_id`** in output so issues map directly to reco's flow blocks

**Full-call (persona mode) — one single call at end:**
- Input: full flow YAML, full transcript, scenario objective
- Evaluates: per-turn block inference + factual accuracy, objective met, overall assessment
- Output: JSON array of per-turn evaluations + overall summary
- **Must infer `block_id` per turn** so issues map to specific blocks
- Advantage: flow YAML sent only once (important for large 14K-line flows)

**End-of-call (both modes) — one call:**
- Input: full transcript, objective, final block status, turn count, duration
- Evaluates: objective_met, overall_assessment (key issues, first failure turn, patterns)
- For scripted: separate call. For persona: included in the single full-call evaluation.

### Block-level issue mapping:
The evaluator and reporter output issues **keyed by block_id** so the team can go directly to the problematic block in the flow YAML and fix it.

Report output:
```
  BLOCK-LEVEL ISSUES:
  Block: check_person (Turn 2) ❌
    HALLUCINATION — Stated business hours not in block content
    Block goal: "本人確認を行う"
    Recommendation: Ground business hours in block or remove

  Block: confirm_date (Turn 3) ❌
    STT MISHEARING — Heard 木曜日 instead of 火曜日
    Block goal: "日時を確認する"
    Recommendation: STT issue, not block content. Consider echo-back logic.

  Block status: greeting ✅ | check_person ❌ | confirm_date ❌ | closing ✅
```

JSON output:
```json
{
  "block_issues": [
    {
      "block_id": "check_person",
      "turn": 2,
      "issue_type": "hallucination",
      "detail": "Stated business hours not in block content",
      "block_goal": "本人確認を行う",
      "agent_response": "...",
      "recommendation": "Ground business hours in block or remove"
    }
  ]
}
```

### Model choices:
| Component | Model | Why |
|-----------|-------|-----|
| Persona caller (real-time) | **GPT-4o-mini** | Cheap, fast, sufficient for conversational Japanese |
| Post-call evaluation (Tier 2) | **Claude Sonnet** | Accurate, best grading quality |
| Live STT (persona mode) | **Whisper** | Real-time, good enough for persona conversation |
| Evaluation STT (Tier 3) | **Whisper** (MVP) → **Google STT v2** (future) | Best Japanese accuracy later |
| TTS for user audio (scripted) | **Cartesia** | Same provider as reco — consistent audio characteristics for reco's STT pipeline |

### Concurrency (swarm mode, future):
```python
async def run_swarm(scenarios, concurrency=5):
    semaphore = asyncio.Semaphore(concurrency)
    async def run_one(scenario):
        async with semaphore:
            return await scenario_runner.run(scenario)
    results = await asyncio.gather(*[run_one(s) for s in scenarios])
```

## Project Structure
```
voiceaiqa/
├── pyproject.toml
├── .env.example
├── config/
│   └── settings.py                 # Env vars: API keys, phone numbers, reco URL
├── models/
│   ├── scenario.py                 # TestScenario, TestStep, Persona
│   └── result.py                   # StepResult, ScenarioResult, all metric results
├── reco/
│   └── client.py                   # RecoClient: trigger calls, fetch transcripts/recordings
├── receivers/
│   ├── base.py                     # CallReceiver interface
│   └── twilio_receiver.py          # Twilio Media Streams + webhook handling
├── core/
│   ├── scenario_runner.py          # Orchestrates: trigger → receive → respond → evaluate
│   ├── vad.py                      # Voice activity detection + latency capture
│   ├── audio_gen.py                # TTS generation for user responses
│   ├── persona_caller.py           # GPT-4o-mini driven caller (persona mode)
│   ├── transcriber.py              # Whisper STT (live + evaluation, pluggable for Google STT later)
│   ├── evaluator.py                # Claude Sonnet grading (Tier 2)
│   ├── metrics.py                  # Tier 1 algorithmic metrics
│   └── reporter.py                 # Console + JSON reports
├── scenarios/
│   ├── example_scripted.yaml
│   └── example_persona.yaml
├── reports/
├── server.py                       # FastAPI webhook server for Twilio
├── tests/
└── cli.py
```

## Build Order
1. **Project skeleton + models** — dataclasses, config, scenario format
2. **Reco client** (`reco/client.py`) — trigger calls, fetch data via localhost
3. **Twilio receiver** (`receivers/twilio_receiver.py` + `server.py`) — Media Streams, answer calls
4. **VAD + turn management** (`core/vad.py`) — 1500ms silence detection, latency capture
5. **Audio generation** (`core/audio_gen.py`) — TTS for scripted user inputs, mulaw conversion
6. **Scenario runner** (`core/scenario_runner.py`) — wire together for scripted mode
7. **Tier 1 metrics** (`core/metrics.py`) — all 6 algorithmic checks
8. **Transcriber** (`core/transcriber.py`) — Whisper with pluggable interface for Google STT later
9. **Evaluator** (`core/evaluator.py`) — Claude Sonnet grading (parallel per-turn for scripted, single call for persona)
10. **Reporter** (`core/reporter.py`) — results output
11. **Persona caller** (`core/persona_caller.py`) — GPT-4o-mini driven caller
12. **CLI** (`cli.py`) — entry point
13. **Swarm mode** — asyncio concurrency

## CLI
```bash
# Terminal 1: Start QA server
python server.py

# Terminal 2: ngrok tunnel (MVP)
ngrok http 8050

# Terminal 3: Run tests
python cli.py run --scenario scenarios/booking_happy.yaml --webhook-url https://abc123.ngrok.io
python cli.py run --scenario scenarios/impatient_customer.yaml --webhook-url https://abc123.ngrok.io
python cli.py run --scenarios-dir scenarios/ --webhook-url https://abc123.ngrok.io
python cli.py report --latest
```

## Future Additions
- **Google Cloud STT v2** for evaluation (best Japanese accuracy)
- **Japanese quality metrics** (keigo, naturalness, business conventions)
- **Semantic completeness** (must_contain_meaning checks)
- **Text simulation layer**: Bypass audio, test via OpenAI Realtime text mode
- **Comparison reports**: Text sim vs E2E for root cause diagnosis
- **SIP receiver**: Replace Twilio ($0 telephony cost)
- **WebRTC receiver**: For browser-based agents
- **Stack-agnostic abstraction**: Generic CallTrigger interface
- **CI/CD integration**: Run QA on every deploy
- **Production monitoring**: Evaluate real production transcripts
- **nginx route** for production Twilio webhook (replace ngrok)

## Verification
1. Reco client: trigger a call via localhost, poll status, fetch transcript + recording
2. Twilio: ngrok tunnel works, webhook receives call, Media Stream connects
3. VAD: correctly detects agent speech → 1500ms silence → turn end
4. Audio gen: Japanese TTS plays cleanly, mulaw conversion correct
5. Scenario runner: full scripted scenario end-to-end
6. Tier 1: all 6 metrics computed correctly
7. Tier 3: Whisper vs reco transcript comparison works
8. Tier 2: Claude grading accurate (scripted parallel + persona single-call)
9. Persona mode: GPT-4o-mini caller has natural conversation
10. Swarm: 5 concurrent calls without interference
