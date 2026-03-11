# Lessons Learned

## Rules
- After ANY correction from the user, add a lesson here
- Write rules that prevent the same mistake
- Review at session start

## Architecture
- reco has NO text API — everything is audio-based, don't try to shortcut with text endpoints
- `final_block_id` is NOT in reco's DB — use Claude inference for MVP, don't assume it exists
- `audioop` is deprecated in Python 3.13+ — always check availability and have fallback
- Reco makes OUTBOUND calls — QA tool must RECEIVE, not initiate
- EC2 is IP-restricted — deploy QA on same instance, use localhost for reco API

## User Preferences
- Don't over-productize — this is an internal reco tool first
- Conservative VAD threshold (1500ms) — no benefit to QA responding fast
- Cheaper models where possible (GPT-4o-mini for persona caller)
- Whisper for STT now, Google STT v2 later
- Block-level issue identification in reports is important

## Process
<!-- Add lessons about the build process as we go -->
