# Agent A: Reco Client

You are building the reco API client for the voiceaiqa QA tool. This component triggers test calls and fetches post-call data from reco's existing REST API.

## Branch
`phase2/reco-client` — cut from latest `main` after Phase 1 is merged.

## Git Workflow
- Cut branch: `git checkout -b phase2/reco-client main`
- Commit incrementally with clear messages (e.g., "Add RecoClient with mock mode")
- Do NOT merge to main — orchestrator handles merges

## Files You Own
```
reco/
├── __init__.py
├── client.py           # RecoClient class (real + mock mode)
└── mock_data.py        # Mock responses for local development
```

## What You Build

### RecoClient class
An async HTTP client that talks to reco's REST API.

```python
class RecoClient:
    def __init__(self, base_url: str, token: str, mock: bool = False):
        ...

    async def start_call(self, phone: str, customer_id: str, flow_path: str, metadata: dict = None) -> CallStartResult:
        """POST /api/calls/start — trigger outbound call.
        Returns call_id and conversation_id.
        Must pass metadata={"qa_test": True, "scenario_id": "..."} to tag QA calls."""

    async def poll_status(self, call_id: str, timeout: float = 60, poll_interval: float = 2) -> str:
        """GET /api/calls/status?call_id=... — poll until completed/failed.
        Returns final status string."""

    async def get_conversation(self, conversation_id: int) -> ConversationData:
        """GET /conversations/{id} — metadata, call_status, duration."""

    async def get_transcript(self, conversation_id: int) -> str:
        """GET /conversations/{id}/transcript — full transcript from S3."""

    async def get_recording_url(self, conversation_id: int) -> str:
        """GET /recordings/{id}/audio — presigned S3 URL for recording."""
```

### Mock mode
When `mock=True`, return realistic fake data so the rest of the pipeline can be developed without a running reco instance.

Mock data should include:
- A multi-turn transcript in Japanese (USER/ASSISTANT format)
- Realistic call metadata (call_status, duration, etc.)
- A fake recording URL (or path to a test audio file)

### Data models
Use dataclasses from `models/result.py` for return types. Import from the shared models — do NOT create your own.

## Reco API Details
- **Base URL**: `http://localhost:3010` (same EC2 instance)
- **Auth**: Bearer token in `Authorization` header
- **Call start**: `POST /api/calls/start` with JSON body `{phone, customer_id, flow_path}`
- **Call status**: `GET /api/calls/status?call_id=...` — returns `running`/`completed`/`failed`/`stopped`
- **Transcript format**: Plain text, `ROLE: text` per line (e.g., `ASSISTANT: お電話ありがとうございます`)
- **Conversation metadata**: Includes `call_status` (success/failure/no_answer/potential)
- Note: `final_block_id` is NOT available yet. Do not depend on it. Add a `get_final_block_id()` method that returns `None` with a TODO comment for when reco adds this field.

## Configuration
Read from `config/settings.py`:
- `RECO_API_URL`
- `RECO_API_TOKEN`
- `RECO_MOCK_MODE` (bool, default False)

## Dependencies
- `httpx` for async HTTP
- Shared models from `models/`

## Testing
Write basic tests in `tests/test_reco_client.py`:
- Test mock mode returns valid data
- Test transcript parsing into structured turns
- Test poll_status timeout behavior

## Acceptance Criteria
- [ ] RecoClient can trigger a call and return call_id/conversation_id (mock mode)
- [ ] RecoClient can poll status until completed (mock mode)
- [ ] RecoClient can fetch transcript and parse into turns
- [ ] RecoClient can fetch recording URL
- [ ] Mock mode works without any network access
- [ ] QA calls are tagged with metadata
- [ ] All tests pass
