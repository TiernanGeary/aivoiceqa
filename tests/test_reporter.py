"""Tests for the Reporter class."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from core.reporter import Reporter, Colors, colored, _severity_for
from models.result import EvalResult, ScenarioResult, StepResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_eval(metric: str, passed: bool, reasoning: str | None = None, score: float | None = None, details: dict | None = None) -> EvalResult:
    return EvalResult(metric=metric, passed=passed, reasoning=reasoning, score=score, details=details)


def _make_step(
    step_number: int,
    passed: bool = True,
    expected_block: str | None = "ask_date",
    actual_block: str | None = "ask_date",
    latency_ms: float = 1200.0,
    evaluations: dict | None = None,
    user_input_text: str = "はい",
    agent_response_text: str = "ご予約の日時をお伺いします。",
    error: str | None = None,
) -> StepResult:
    if evaluations is None:
        evaluations = {
            "block_correct": _make_eval("block_correct", passed),
            "factual_accuracy": _make_eval("factual_accuracy", True, reasoning="Correct info"),
        }
    return StepResult(
        step_number=step_number,
        user_input_text=user_input_text,
        agent_response_text=agent_response_text,
        expected_block=expected_block,
        actual_block=actual_block,
        latency_ms=latency_ms,
        evaluations=evaluations,
        passed=passed,
        error=error,
    )


def _make_scenario(
    steps: list[StepResult] | None = None,
    overall_passed: bool = True,
    scenario_id: str = "booking_happy_path",
    test_mode: str = "scripted",
    duration_s: float = 45.2,
    latency_p50_ms: float = 1200.0,
    latency_p95_ms: float = 2100.0,
) -> ScenarioResult:
    if steps is None:
        steps = [_make_step(1), _make_step(2)]
    return ScenarioResult(
        scenario_id=scenario_id,
        test_mode=test_mode,
        steps=steps,
        overall_passed=overall_passed,
        started_at=datetime(2026, 3, 10, 14, 30, 0, tzinfo=timezone.utc),
        duration_s=duration_s,
        latency_p50_ms=latency_p50_ms,
        latency_p95_ms=latency_p95_ms,
    )


# ---------------------------------------------------------------------------
# Colors / helpers
# ---------------------------------------------------------------------------

class TestColors:
    def test_colored_wraps_text(self):
        result = colored("hello", Colors.GREEN)
        assert Colors.GREEN in result
        assert Colors.RESET in result
        assert "hello" in result

    def test_severity_critical(self):
        assert _severity_for("block_correct") == "critical"
        assert _severity_for("hallucination") == "critical"
        assert _severity_for("stuck_loop") == "critical"

    def test_severity_warning(self):
        assert _severity_for("keigo_level") == "warning"
        assert _severity_for("must_contain_meaning") == "warning"

    def test_severity_info_default(self):
        assert _severity_for("factual_accuracy") == "info"
        assert _severity_for("naturalness") == "info"


# ---------------------------------------------------------------------------
# print_summary
# ---------------------------------------------------------------------------

class TestPrintSummary:
    def test_prints_scenario_id(self, capsys):
        r = _make_scenario()
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert "booking_happy_path" in out

    def test_prints_pass_count(self, capsys):
        r = _make_scenario()
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert "2/2 passed" in out

    def test_prints_duration(self, capsys):
        r = _make_scenario()
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert "45.2s" in out

    def test_prints_latency(self, capsys):
        r = _make_scenario()
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert "1200ms" in out
        assert "2100ms" in out

    def test_prints_na_latency_when_none(self, capsys):
        r = _make_scenario(latency_p50_ms=None, latency_p95_ms=None)
        r.latency_p50_ms = None
        r.latency_p95_ms = None
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert "N/A" in out

    def test_overall_pass_color(self, capsys):
        r = _make_scenario(overall_passed=True)
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert Colors.GREEN in out

    def test_overall_fail_color(self, capsys):
        r = _make_scenario(overall_passed=False)
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert Colors.RED in out

    def test_mixed_pass_fail_count(self, capsys):
        steps = [_make_step(1, passed=True), _make_step(2, passed=False)]
        r = _make_scenario(steps=steps, overall_passed=False)
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert "1/2 passed" in out


# ---------------------------------------------------------------------------
# print_detailed
# ---------------------------------------------------------------------------

class TestPrintDetailed:
    def test_prints_each_step(self, capsys):
        r = _make_scenario()
        Reporter().print_detailed(r)
        out = capsys.readouterr().out
        assert "Step 1" in out
        assert "Step 2" in out

    def test_prints_agent_response(self, capsys):
        r = _make_scenario()
        Reporter().print_detailed(r)
        out = capsys.readouterr().out
        assert "ご予約の日時をお伺いします" in out

    def test_prints_block_info(self, capsys):
        r = _make_scenario()
        Reporter().print_detailed(r)
        out = capsys.readouterr().out
        assert "ask_date" in out

    def test_prints_metric_names(self, capsys):
        r = _make_scenario()
        Reporter().print_detailed(r)
        out = capsys.readouterr().out
        assert "block_correct" in out
        assert "factual_accuracy" in out

    def test_prints_error_when_present(self, capsys):
        step = _make_step(1, error="Timeout waiting for response")
        r = _make_scenario(steps=[step])
        Reporter().print_detailed(r)
        out = capsys.readouterr().out
        assert "Timeout waiting for response" in out

    def test_handles_no_evaluations(self, capsys):
        step = _make_step(1, evaluations={})
        r = _make_scenario(steps=[step])
        Reporter().print_detailed(r)
        out = capsys.readouterr().out
        assert "Step 1" in out

    def test_handles_no_block(self, capsys):
        step = _make_step(1, expected_block=None, actual_block=None)
        r = _make_scenario(steps=[step])
        Reporter().print_detailed(r)
        out = capsys.readouterr().out
        assert "Step 1" in out


# ---------------------------------------------------------------------------
# print_comparison
# ---------------------------------------------------------------------------

class TestPrintComparison:
    def _make_pair(self):
        text_evals = {
            "block_correct": _make_eval("block_correct", True),
            "factual_accuracy": _make_eval("factual_accuracy", True),
        }
        e2e_evals = {
            "block_correct": _make_eval("block_correct", True),
            "factual_accuracy": _make_eval("factual_accuracy", False),
        }
        text_step = _make_step(1, evaluations=text_evals)
        e2e_step = _make_step(1, evaluations=e2e_evals)
        text_result = _make_scenario(steps=[text_step])
        e2e_result = _make_scenario(steps=[e2e_step])
        return text_result, e2e_result

    def test_diagnoses_stt_tts_issue(self, capsys):
        text_r, e2e_r = self._make_pair()
        Reporter().print_comparison(text_r, e2e_r)
        out = capsys.readouterr().out
        assert "STT/TTS issue" in out

    def test_diagnoses_prompt_flow_issue(self, capsys):
        text_evals = {"block_correct": _make_eval("block_correct", False)}
        e2e_evals = {"block_correct": _make_eval("block_correct", False)}
        t = _make_scenario(steps=[_make_step(1, evaluations=text_evals)])
        e = _make_scenario(steps=[_make_step(1, evaluations=e2e_evals)])
        Reporter().print_comparison(t, e)
        out = capsys.readouterr().out
        assert "Prompt/flow issue" in out

    def test_diagnoses_text_only_anomaly(self, capsys):
        text_evals = {"block_correct": _make_eval("block_correct", False)}
        e2e_evals = {"block_correct": _make_eval("block_correct", True)}
        t = _make_scenario(steps=[_make_step(1, evaluations=text_evals)])
        e = _make_scenario(steps=[_make_step(1, evaluations=e2e_evals)])
        Reporter().print_comparison(t, e)
        out = capsys.readouterr().out
        assert "Text-only anomaly" in out

    def test_both_pass_shows_dash(self, capsys):
        evals = {"block_correct": _make_eval("block_correct", True)}
        t = _make_scenario(steps=[_make_step(1, evaluations=evals)])
        e = _make_scenario(steps=[_make_step(1, evaluations=evals)])
        Reporter().print_comparison(t, e)
        # The dash character for "no issue"
        out = capsys.readouterr().out
        # When both pass, diagnosis is plain dash
        assert "—" in out


# ---------------------------------------------------------------------------
# generate_json_report
# ---------------------------------------------------------------------------

class TestGenerateJsonReport:
    def test_top_level_fields(self):
        r = _make_scenario()
        report = Reporter().generate_json_report(r)
        assert report["scenario_id"] == "booking_happy_path"
        assert report["test_mode"] == "scripted"
        assert report["duration_s"] == 45.2
        assert report["overall_passed"] is True
        assert "timestamp" in report
        assert "summary" in report
        assert "block_issues" in report
        assert "steps" in report

    def test_summary_counts(self):
        steps = [_make_step(1, passed=True), _make_step(2, passed=False)]
        r = _make_scenario(steps=steps, overall_passed=False)
        report = Reporter().generate_json_report(r)
        summary = report["summary"]
        assert summary["total_steps"] == 2
        assert summary["passed_steps"] == 1
        assert summary["failed_steps"] == 1
        assert summary["latency_p50_ms"] == 1200.0
        assert summary["latency_p95_ms"] == 2100.0

    def test_steps_structure(self):
        r = _make_scenario()
        report = Reporter().generate_json_report(r)
        step = report["steps"][0]
        assert step["step"] == 1
        assert step["user_input"] == "はい"
        assert step["agent_response"] is not None
        assert step["expected_block"] == "ask_date"
        assert step["inferred_block"] == "ask_date"
        assert step["latency_ms"] == 1200.0
        assert "block_correct" in step["evaluations"]

    def test_evaluation_entry_fields(self):
        evals = {
            "factual": _make_eval("factual", True, reasoning="Good", score=0.95, details={"key": "val"}),
        }
        step = _make_step(1, evaluations=evals)
        r = _make_scenario(steps=[step])
        report = Reporter().generate_json_report(r)
        ev = report["steps"][0]["evaluations"]["factual"]
        assert ev["passed"] is True
        assert ev["reasoning"] == "Good"
        assert ev["score"] == 0.95
        assert ev["details"] == {"key": "val"}

    def test_report_is_json_serializable(self):
        r = _make_scenario()
        report = Reporter().generate_json_report(r)
        # Should not raise
        serialized = json.dumps(report, default=str)
        assert isinstance(serialized, str)

    def test_block_issues_included(self):
        step = _make_step(1, passed=False, expected_block="confirm_booking")
        r = _make_scenario(steps=[step], overall_passed=False)
        report = Reporter().generate_json_report(r)
        assert "confirm_booking" in report["block_issues"]


# ---------------------------------------------------------------------------
# generate_block_issue_map
# ---------------------------------------------------------------------------

class TestBlockIssueMap:
    def test_groups_by_block(self):
        s1 = _make_step(1, passed=False, expected_block="ask_date")
        s2 = _make_step(2, passed=False, expected_block="confirm_booking")
        r = _make_scenario(steps=[s1, s2], overall_passed=False)
        bmap = Reporter().generate_block_issue_map(r)
        assert "ask_date" in bmap
        assert "confirm_booking" in bmap

    def test_issues_contain_metric_and_severity(self):
        evals = {
            "block_correct": _make_eval("block_correct", False, reasoning="Wrong block"),
        }
        step = _make_step(1, passed=False, expected_block="ask_date", evaluations=evals)
        r = _make_scenario(steps=[step], overall_passed=False)
        bmap = Reporter().generate_block_issue_map(r)
        issues = bmap["ask_date"]["issues"]
        assert len(issues) == 1
        assert issues[0]["metric"] == "block_correct"
        assert issues[0]["severity"] == "critical"
        assert issues[0]["detail"] == "Wrong block"
        assert issues[0]["step"] == 1

    def test_pass_rate_computed(self):
        evals = {
            "block_correct": _make_eval("block_correct", True),
            "factual": _make_eval("factual", False),
            "keigo_level": _make_eval("keigo_level", True),
            "hallucination": _make_eval("hallucination", True),
        }
        step = _make_step(1, expected_block="ask_date", evaluations=evals)
        r = _make_scenario(steps=[step])
        bmap = Reporter().generate_block_issue_map(r)
        assert bmap["ask_date"]["pass_rate"] == 0.75

    def test_all_pass_rate_one(self):
        evals = {"block_correct": _make_eval("block_correct", True)}
        step = _make_step(1, expected_block="ask_date", evaluations=evals)
        r = _make_scenario(steps=[step])
        bmap = Reporter().generate_block_issue_map(r)
        assert bmap["ask_date"]["pass_rate"] == 1.0
        assert bmap["ask_date"]["issues"] == []

    def test_no_block_skips_step(self):
        step = _make_step(1, expected_block=None, actual_block=None)
        r = _make_scenario(steps=[step])
        bmap = Reporter().generate_block_issue_map(r)
        assert bmap == {}

    def test_uses_actual_block_as_fallback(self):
        step = _make_step(1, expected_block=None, actual_block="greeting")
        r = _make_scenario(steps=[step])
        bmap = Reporter().generate_block_issue_map(r)
        assert "greeting" in bmap


# ---------------------------------------------------------------------------
# aggregate_block_issues
# ---------------------------------------------------------------------------

class TestAggregateBlockIssues:
    def test_aggregates_across_runs(self):
        evals_pass = {"block_correct": _make_eval("block_correct", True)}
        evals_fail = {"block_correct": _make_eval("block_correct", False, reasoning="Wrong")}
        r1 = _make_scenario(steps=[_make_step(1, expected_block="ask_date", evaluations=evals_pass)])
        r2 = _make_scenario(steps=[_make_step(1, expected_block="ask_date", evaluations=evals_fail)])
        agg = Reporter().aggregate_block_issues([r1, r2])
        assert "ask_date" in agg
        assert agg["ask_date"]["pass_rate"] == 0.5  # avg of 1.0 and 0.0
        assert len(agg["ask_date"]["issues"]) == 1  # one failure

    def test_empty_results(self):
        agg = Reporter().aggregate_block_issues([])
        assert agg == {}

    def test_multiple_blocks_across_runs(self):
        e1 = {"block_correct": _make_eval("block_correct", True)}
        e2 = {"block_correct": _make_eval("block_correct", False)}
        r1 = _make_scenario(steps=[_make_step(1, expected_block="ask_date", evaluations=e1)])
        r2 = _make_scenario(steps=[_make_step(1, expected_block="confirm", evaluations=e2)])
        agg = Reporter().aggregate_block_issues([r1, r2])
        assert "ask_date" in agg
        assert "confirm" in agg


# ---------------------------------------------------------------------------
# save_report
# ---------------------------------------------------------------------------

class TestSaveReport:
    def test_saves_json_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = Reporter(output_dir=tmpdir)
            r = _make_scenario()
            path = reporter.save_report(r, "booking_happy_path")
            assert os.path.exists(path)
            assert path.startswith(tmpdir)
            assert "booking_happy_path" in path
            assert path.endswith(".json")

            with open(path) as f:
                data = json.load(f)
            assert data["scenario_id"] == "booking_happy_path"

    def test_creates_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sub", "reports")
            reporter = Reporter(output_dir=nested)
            r = _make_scenario()
            path = reporter.save_report(r, "test")
            assert os.path.exists(path)

    def test_filename_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = Reporter(output_dir=tmpdir)
            r = _make_scenario()
            path = reporter.save_report(r, "my_scenario")
            filename = os.path.basename(path)
            assert filename.startswith("my_scenario_")
            assert filename.endswith(".json")


# ---------------------------------------------------------------------------
# save_comparison_report
# ---------------------------------------------------------------------------

class TestSaveComparisonReport:
    def test_saves_comparison(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = Reporter(output_dir=tmpdir)
            t = _make_scenario()
            e = _make_scenario()
            path = reporter.save_comparison_report(t, e, "booking")
            assert os.path.exists(path)
            assert "comparison" in path

            with open(path) as f:
                data = json.load(f)
            assert data["scenario_id"] == "booking"
            assert "text_result" in data
            assert "e2e_result" in data
            assert "comparisons" in data

    def test_comparison_diagnosis_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = Reporter(output_dir=tmpdir)
            text_evals = {"m1": _make_eval("m1", True)}
            e2e_evals = {"m1": _make_eval("m1", False)}
            t = _make_scenario(steps=[_make_step(1, evaluations=text_evals)])
            e = _make_scenario(steps=[_make_step(1, evaluations=e2e_evals)])
            path = reporter.save_comparison_report(t, e, "test")
            with open(path) as f:
                data = json.load(f)
            comp = data["comparisons"][0]
            assert comp["diagnosis"] == "STT/TTS issue"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_scenario(self, capsys):
        r = _make_scenario(steps=[], overall_passed=True, duration_s=0.0,
                           latency_p50_ms=None, latency_p95_ms=None)
        r.latency_p50_ms = None
        r.latency_p95_ms = None
        Reporter().print_summary(r)
        out = capsys.readouterr().out
        assert "0/0 passed" in out

    def test_empty_scenario_json(self):
        r = _make_scenario(steps=[], overall_passed=True)
        report = Reporter().generate_json_report(r)
        assert report["summary"]["total_steps"] == 0
        assert report["steps"] == []

    def test_step_with_none_latency(self, capsys):
        step = _make_step(1, passed=True)
        step.latency_ms = None
        r = _make_scenario(steps=[step])
        Reporter().print_detailed(r)
        out = capsys.readouterr().out
        assert "Step 1" in out
