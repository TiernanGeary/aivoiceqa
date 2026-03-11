# voiceaiqa — Project Instructions

## What This Is
Automated QA tool for Japanese outbound voice agents (reco). Receives real phone calls via Twilio, plays scripted audio responses, evaluates agent behavior across 13 metrics.

## Architecture
- **Reco connection**: REST API on localhost:3010 (same EC2), bearer token auth
- **Call flow**: Trigger reco outbound → reco calls our Twilio number → WebSocket audio stream → VAD turn detection → play response → evaluate
- **Two modes**: Scripted (MVP) and Persona (future)
- **Three eval tiers**: Algorithmic (free) → Claude grading → Whisper audio analysis

## Agent Build System
This project is built by a team of specialized agents, coordinated by an orchestrator.

### Agent Specs
All agent specs live in `.claude/agents/`. Read the relevant spec before working on a component.

### Progress Tracking
- **Build status**: `tasks/status.md` — current phase, what's done, what's blocked
- **Plan**: `tasks/todo.md` — full architecture and plan
- **Lessons**: `tasks/lessons.md` — mistakes and patterns to avoid

### Rules for All Agents
1. **Check status first**: Read `tasks/status.md` before starting work to know current state
2. **Update status**: Mark your component done in `tasks/status.md` when complete
3. **Run tests**: `pytest tests/` must pass before declaring done
4. **Don't modify other agents' files** unless fixing a clear interface bug
5. **Use mock mode** for development — no external API keys needed
6. **Commit incrementally** with clear messages describing what changed
7. **Follow your spec** in `.claude/agents/` — it defines your interface contract

### Key File Paths
- Models: `models/scenario.py`, `models/result.py`
- Config: `config/settings.py`, `.env`
- Scenarios: `scenarios/*.yaml`
- Reports: `reports/*.json`
- Agent specs: `.claude/agents/*.md`
- Reco backend (read-only reference): `/Users/tiernangeary/Downloads/realtime-tmp/reco-rta/`

## Code Style
- Python 3.11+
- Async where I/O is involved (httpx, websockets)
- Dataclasses for models (not Pydantic for now — keep deps minimal)
- Type hints on all function signatures
- No unnecessary abstractions — build what's needed now

## Testing
- `pytest` + `pytest-asyncio`
- Mock mode must work with zero env vars
- Test files mirror source: `core/vad.py` → `tests/test_vad.py`
