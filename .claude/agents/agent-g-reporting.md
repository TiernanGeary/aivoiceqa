# Agent G: Reporting

You are building the reporting component for the voiceaiqa QA tool. This component generates human-readable console output, structured JSON reports, and block-level issue mapping.

## Branch
`phase4/reporting` — cut from latest `main` after Phase 3 is merged.

## Git Workflow
- Cut branch: `git checkout -b phase4/reporting main`
- Commit incrementally with clear messages
- Do NOT merge to main — orchestrator handles merges

## Files You Own
```
core/
└── reporter.py           # Reporter class — console + JSON + block mapping
reports/                   # Output directory for generated reports
└── .gitkeep
```

## What You Build

### Reporter class (core/reporter.py)

```python
class Reporter:
    def __init__(self, output_dir: str = "reports"):
        ...

    def print_summary(self, result: ScenarioResult) -> None:
        """Print concise pass/fail summary to console.
        Example:
          Scenario: booking_happy_path (scripted)
          Steps: 5/5 passed
          Duration: 45.2s
          Latency P50: 1.2s | P95: 2.1s
        """

    def print_detailed(self, result: ScenarioResult) -> None:
        """Print step-by-step results to console with formatting.
        Shows each step, all metric evaluations, and failure details.
        Uses color: green for pass, red for fail, yellow for warnings."""

    def print_comparison(self, text_result: ScenarioResult, e2e_result: ScenarioResult) -> None:
        """Print side-by-side comparison of text sim vs E2E results.
        Auto-diagnoses root causes:
          - Text PASS + E2E FAIL → 'STT/TTS issue'
          - Both FAIL → 'Prompt/flow issue'
          - Text FAIL + E2E PASS → 'Text-only anomaly'

        Output format:
        ┌────────┬──────────────────┬──────────┬──────────┬─────────────────┐
        │ Step   │ Check            │ Text Sim │ E2E Voice│ Diagnosis       │
        ├────────┼──────────────────┼──────────┼──────────┼─────────────────┤
        │ 1      │ block_correct    │ PASS     │ PASS     │ —               │
        │ 2      │ factual          │ PASS     │ FAIL     │ STT/TTS issue   │
        └────────┴──────────────────┴──────────┴──────────┴─────────────────┘
        """

    def generate_json_report(self, result: ScenarioResult) -> dict:
        """Generate structured JSON report. Includes all metrics, timing, evaluations."""

    def generate_block_issue_map(self, result: ScenarioResult) -> dict:
        """Map failures to specific flow blocks.

        Output format:
        {
          "ask_date": {
            "block_id": "ask_date",
            "issues": [
              {
                "metric": "keigo_level_correct",
                "severity": "warning",
                "detail": "Expected teineigo, detected casual",
                "step": 2
              }
            ],
            "pass_rate": 0.75
          },
          "confirm_booking": {
            "block_id": "confirm_booking",
            "issues": [...],
            "pass_rate": 0.5
          }
        }

        This lets the team quickly find which flow blocks need fixing.
        """

    def save_report(self, result: ScenarioResult, scenario_id: str) -> str:
        """Save full report to disk. Returns file path.
        Saves as: reports/{scenario_id}_{timestamp}.json
        """

    def save_comparison_report(
        self, text_result: ScenarioResult, e2e_result: ScenarioResult, scenario_id: str
    ) -> str:
        """Save comparison report to disk. Returns file path."""
```

### Console Formatting

Use ANSI color codes for terminal output (no external dependency):
```python
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

def colored(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"
```

### Detailed Step Output Format
```
Step 1: User says "はい、予約をお願いします"
  Agent response: "お電話ありがとうございます。ご予約の日時をお伺いしてもよろしいでしょうか。"
  Expected block: ask_date | Inferred: ask_date  ✅
  Latency: 1.2s
  ┌─────────────────────┬────────┬──────────────────────────────────┐
  │ Metric              │ Result │ Detail                           │
  ├─────────────────────┼────────┼──────────────────────────────────┤
  │ block_correct       │ ✅ PASS │                                  │
  │ factual_accuracy    │ ✅ PASS │ Correctly asks for date/time     │
  │ keigo_level         │ ✅ PASS │ teineigo detected                │
  │ hallucination       │ ✅ PASS │ No hallucination                 │
  │ must_contain_meaning│ ✅ PASS │ Contains: 日時                   │
  └─────────────────────┴────────┴──────────────────────────────────┘
```

### JSON Report Structure
```json
{
  "scenario_id": "booking_happy_path",
  "test_mode": "e2e",
  "timestamp": "2026-03-10T14:30:00Z",
  "duration_s": 45.2,
  "overall_passed": false,
  "summary": {
    "total_steps": 5,
    "passed_steps": 4,
    "failed_steps": 1,
    "latency_p50_ms": 1200,
    "latency_p95_ms": 2100
  },
  "block_issues": {
    "confirm_booking": {
      "issues": [...],
      "pass_rate": 0.5
    }
  },
  "steps": [
    {
      "step": 1,
      "user_input": "はい、予約をお願いします",
      "agent_response": "...",
      "expected_block": "ask_date",
      "inferred_block": "ask_date",
      "latency_ms": 1200,
      "evaluations": {
        "block_correct": {"passed": true},
        "factual_accuracy": {"passed": true, "reasoning": "..."},
        ...
      }
    }
  ]
}
```

### Block Issue Map Details

Severity levels:
- **`critical`**: Block transition wrong, hallucination detected, stuck loop
- **`warning`**: Keigo incorrect, missing required meaning, high latency
- **`info`**: Minor naturalness issues, clarification requests

Aggregate across multiple scenario runs:
```python
def aggregate_block_issues(self, results: list[ScenarioResult]) -> dict:
    """Aggregate block issues across multiple scenario runs.
    Shows which blocks consistently fail and at what rate."""
```

## Configuration
- `REPORT_OUTPUT_DIR` (default "reports")

## Dependencies
- Standard library only (no external deps for reporting)
- Shared models from `models/`

## Testing
Write tests in `tests/test_reporter.py`:
- Test console summary output (capture stdout)
- Test JSON report structure matches expected schema
- Test block issue mapping correctly groups failures by block
- Test comparison table with known pass/fail combinations
- Test severity classification
- Test report file saving
- Test aggregation across multiple results

## Acceptance Criteria
- [ ] Console summary prints pass/fail with color
- [ ] Detailed step output shows all metrics per step
- [ ] Comparison table correctly diagnoses root causes
- [ ] JSON report contains all data needed for programmatic analysis
- [ ] Block issue map groups failures by block_id with severity
- [ ] Reports save to disk with timestamp naming
- [ ] Aggregate reporting works across multiple scenario runs
- [ ] All tests pass
