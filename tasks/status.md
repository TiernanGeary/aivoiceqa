# Build Status

## Current Phase: Phase 4
**Next action**: Spawn Agents F + G (Evaluation + Reporting)

## Phase Status

### Phase 1: Foundation — COMPLETE
- [x] `pyproject.toml`
- [x] `config/settings.py`
- [x] `models/scenario.py`
- [x] `models/result.py`
- [x] `scenarios/example_scripted.yaml`
- [x] `.env.example`
- **Test gate**: 13/13 passed
- **User action**: Create `.env` from `.env.example` (just `RECO_MOCK_MODE=true` for mock)

### Phase 2: Core Components — COMPLETE
- [x] Agent A: reco-client (`reco/client.py`) — 17 tests
- [x] Agent B: twilio-receiver (`receivers/`, `server.py`) — 27 tests
- [x] Agent C: vad-engine (`core/vad.py`, `core/audio_utils.py`) — 25 tests
- [x] Agent D: audio-gen (`core/audio_gen.py`, `core/audio_cache.py`) — 29 tests
- **Test gate**: 111/111 passed, all components merged cleanly
- **User action**: None for mock mode. For real calls: Twilio setup + ngrok

### Phase 3: Integration — COMPLETE
- [x] Agent E: scenario-runner (`core/scenario_runner.py`, `cli.py`) — 18 tests
- **Test gate**: 129/129 passed, CLI mock pipeline runs e2e
- **User action**: None for mock mode

### Phase 4: Evaluation & Reporting — NOT STARTED
- [ ] Agent F: evaluation (`core/evaluator.py`, `core/tier1_metrics.py`, `core/tier2_metrics.py`, `core/tier3_metrics.py`)
- [ ] Agent G: reporting (`core/reporter.py`)
- **Test gate**: Tier 1 eval works, JSON report valid, all prior tests pass
- **User action**: `ANTHROPIC_API_KEY` needed for Tier 2 eval

### Phase 5: QA & Verification — NOT STARTED
- [ ] QA Agent: integration tests, full verification checklist
- **Test gate**: Full checklist in `qa-agent.md`

## Merge Log
| Date | Phase | Test Gate |
|------|-------|-----------|
| 2026-03-10 | Phase 1 merged to main | pytest: 13/13 passed |
| 2026-03-10 | Phase 2 merged to main (4 agents) | pytest: 111/111 passed |
| 2026-03-10 | Phase 3 merged to main | pytest: 129/129 passed + CLI smoke test |

## Blockers
<!-- Record any issues blocking progress -->
None yet.

## Decisions Made
<!-- Record architectural decisions for future reference -->
- MVP is scripted mode only (no persona mode)
- Mock mode for all development (no API keys needed)
- 1500ms VAD silence threshold (conservative for Japanese)
- Claude Sonnet for Tier 2 eval, Whisper for Tier 3
- Block inference via Claude (final_block_id not in reco DB yet)
- Deploy on same EC2 as reco, ngrok for MVP webhooks
