# Reco Change: Persist final_block_id and final_block_status

## Why
The QA tool needs to know which block the agent ended on (and its status) to evaluate block-level correctness. Currently these values exist in-memory on the orchestrator but are never saved to the database.

## Impact
- Enables algorithmic (instant, free) block correctness checking
- Currently the QA tool infers blocks via LLM which is ~90-95% accurate
- This change makes it 100% accurate and moves it to a free Tier 1 metric

## Scope
6 files, ~12 lines of code added. No breaking changes. New columns are nullable.

---

## Changes (6 files)

### 1. `api/models/conversations.py` — Add 2 columns
After the `call_status` column (around line 24), add:
```python
final_block_id = Column(String(255), nullable=True)
final_block_status = Column(String(50), nullable=True)
```

### 2. `api/schemas.py` — Add 2 fields to ConversationBase
After the `call_status` field (around line 45), add:
```python
final_block_id: str | None = None
final_block_status: str | None = None
```
`ConversationDataCreate` and `ConversationData` inherit from `ConversationBase`, so they pick these up automatically.

### 3. `api/services/realtime_logging.py` — Add 2 parameters to log_call_finish()
Add to function signature (around line 102):
```python
def log_call_finish(
    *,
    conversation_id: int,
    user_id: int,
    call_status: str,
    call_id: Optional[str] = None,
    summary: str | None,
    call_status_reason: str | None = None,
    recording_path: Optional[str] = None,
    recording_duration: Optional[float] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    full_transcript: Optional[str] = None,
    final_block_id: Optional[str] = None,        # ← NEW
    final_block_status: Optional[str] = None,     # ← NEW
) -> None:
```

Add to the `ConversationDataCreate` payload (around line 293):
```python
payload = schemas.ConversationDataCreate(
    # ... existing fields ...
    call_status=call_status,
    final_block_id=final_block_id,            # ← NEW
    final_block_status=final_block_status,     # ← NEW
)
```

### 4. `api/crud.py` — Add 2 assignments in update_conversation()
After the `call_status` assignment (around line 589), add:
```python
db_conversation.final_block_id = conversation.final_block_id  # type: ignore
db_conversation.final_block_status = conversation.final_block_status  # type: ignore
```

### 5. `reco-rta/reco_rta/realtime.py` — Pass values in _persist_final_result()
In the `log_call_finish()` call inside `_persist_final_result()` (around line 2519), add:
```python
realtime_logging.log_call_finish(
    # ... existing params ...
    full_transcript=transcript,
    final_block_id=self.final_block_id,          # ← NEW
    final_block_status=self.final_block_status,   # ← NEW
)
```

### 6. Database migration
Create an Alembic migration:
```python
"""add final_block_id and final_block_status to conversations"""

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('conversations', sa.Column('final_block_id', sa.String(255), nullable=True))
    op.add_column('conversations', sa.Column('final_block_status', sa.String(50), nullable=True))

def downgrade():
    op.drop_column('conversations', 'final_block_status')
    op.drop_column('conversations', 'final_block_id')
```

---

## Data flow after change
```
RealtimeOrchestrator sets self.final_block_id / self.final_block_status
  → _persist_final_result() passes them to log_call_finish()
    → log_call_finish() includes them in ConversationDataCreate payload
      → crud.update_conversation() saves them to the Conversation model
        → Stored in PostgreSQL
          → Available via GET /conversations/{id} API response
```

## Values stored
- `final_block_id`: string, e.g. "greeting", "confirm_date", "closing"
- `final_block_status`: string, e.g. "success", "failure", "no_answer", "potential"

Both nullable — existing conversations will have NULL values.
