# Voice Agent QA — Evaluation Metrics

## Overview
Each test call is evaluated across 3 tiers of metrics. Tier 1 runs instantly for free. Tier 2 uses Claude for deeper analysis. Tier 3 checks audio quality via Whisper.

Two test modes share the same metrics where applicable:
- **Scripted**: Pre-defined user inputs, checks specific flow blocks
- **Persona**: LLM-driven caller with a character description, holistic evaluation

---

## MVP Metrics (12 total)

### Tier 1: Algorithmic — Instant, $0

| # | Metric | What It Catches | How It's Measured | Scripted | Persona |
|---|--------|----------------|-------------------|:--------:|:-------:|
| 1 | **Block Correctness** | Agent ended on wrong flow block | Compare `final_block_id` from reco vs expected terminal block | ✅ | — |
| 2 | **Turn Count** | Agent looping or skipping steps | Count transcript turns, compare to expected min/max range | ✅ | ✅ |
| 3 | **Call Duration** | Call too short (dropped) or too long (stuck) | `end_time - start_time`, compare to expected range | ✅ | ✅ |
| 4 | **Latency** | Slow agent response times | Measure silence gap between user finishing → agent starting (VAD timestamps) | ✅ | ✅ |
| 5 | **Repetition Detection** | Agent repeating itself (stuck in loop) | String similarity between consecutive agent turns (>80% = flagged) | ✅ | ✅ |
| 6 | **Flow Completion** | Call didn't reach a terminal state | Check `final_block_status`: success / failure / no_answer / potential | ✅ | ✅ |

### Tier 2: LLM-Graded (Claude) — ~$0.15/call

| # | Metric | What It Catches | How It's Measured | Scripted | Persona |
|---|--------|----------------|-------------------|:--------:|:-------:|
| 7 | **Factual Accuracy** | Hallucinated info (wrong dates, prices, policies) | Claude compares agent response vs flow block content. Flags claims not in the flow. Persona mode: post-call block inference. | ✅ | ✅ |
| 8 | **Instruction Following** | Agent didn't do what the block says to do | Claude checks if agent accomplished the block's goal | ✅ | — |
| 9 | **Objective Met** | Call failed its purpose | Claude evaluates full transcript against the scenario's objective | ✅ | ✅ |
| 10 | **STT Inference** | Agent misheard the caller (STT error) | Claude checks: does agent response make sense given what we said? If not → suspected STT issue | ✅ | ✅ |

### Tier 3: Audio-Specific — ~$0.02/call

| # | Metric | What It Catches | How It's Measured | Scripted | Persona |
|---|--------|----------------|-------------------|:--------:|:-------:|
| 11 | **TTS Accuracy** | Cartesia TTS said something different from intended text | Compare reco transcript (intended) vs Whisper transcription of recorded audio | ✅ | ✅ |
| 12 | **TTS Intelligibility** | Garbled or unclear agent speech | Whisper confidence scores on agent audio — low confidence = bad TTS output | ✅ | ✅ |

---

## Post-MVP Metrics (planned)

| # | Metric | What It Catches | How It's Measured |
|---|--------|----------------|-------------------|
| 13 | **Japanese Quality** | Wrong keigo, unnatural phrasing, missing business phone conventions | Claude grades: keigo level, naturalness, business phone etiquette |
| 14 | **Semantic Completeness** | Agent missed required information in response | Claude checks `must_contain_meaning` list with semantic matching (午後2時 = 14時) |

---

## Metric Categories at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│                    WHAT WE'RE TESTING                        │
├──────────────────┬──────────────────┬───────────────────────┤
│   Flow Logic     │   Content        │   Audio Pipeline      │
│                  │                  │                       │
│ 1. Block correct │ 7. Factual acc.  │ 10. STT inference     │
│ 2. Turn count    │ 8. Instruction   │ 11. TTS accuracy      │
│ 3. Duration      │ 9. Objective met │ 12. TTS intelligibility│
│ 4. Latency       │                  │                       │
│ 5. Repetition    │                  │                       │
│ 6. Completion    │                  │                       │
├──────────────────┴──────────────────┴───────────────────────┤
│                  FUTURE                                      │
│ 13. Japanese quality  │  14. Semantic completeness           │
└─────────────────────────────────────────────────────────────┘
```

## Cost Per Test Call (~3 min)

| Component | Cost |
|-----------|------|
| OpenAI Realtime API (agent side) | ~$0.90 |
| Twilio inbound (Japan) | ~$0.17 |
| Claude evaluation (Sonnet) | ~$0.15 |
| Whisper transcription | ~$0.02 |
| **Total per call** | **~$1.24** |

| Scale | Cost |
|-------|------|
| 10 calls | ~$12 |
| 50 calls | ~$62 |
| 100 calls (swarm batch) | ~$124 |

## Root Cause Diagnosis (future: text sim comparison)

When text simulation layer is added, running the same scenario through both layers enables automatic root cause identification:

| Text Sim | Voice E2E | Diagnosis |
|----------|-----------|-----------|
| ✅ Pass | ❌ Fail | **STT/TTS issue** — audio pipeline problem |
| ❌ Fail | ❌ Fail | **Prompt/flow issue** — logic or hallucination bug |
| ❌ Fail | ✅ Pass | **Text-only anomaly** — investigate |
| ✅ Pass | ✅ Pass | **All good** |
