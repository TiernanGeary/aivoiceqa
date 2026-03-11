"""Reporting module — console output, JSON reports, and block issue mapping."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from models.result import EvalResult, ScenarioResult, StepResult


# ---------------------------------------------------------------------------
# ANSI helpers (no external deps)
# ---------------------------------------------------------------------------

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def colored(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

_CRITICAL_METRICS = {"block_correct", "hallucination", "stuck_loop"}
_WARNING_METRICS = {"keigo_level", "must_contain_meaning", "latency"}
# Everything else defaults to "info"


def _severity_for(metric: str) -> str:
    if metric in _CRITICAL_METRICS:
        return "critical"
    if metric in _WARNING_METRICS:
        return "warning"
    return "info"


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

class Reporter:
    """Generate console and JSON reports from ScenarioResult data."""

    def __init__(self, output_dir: str = "reports") -> None:
        self.output_dir = output_dir

    # ------------------------------------------------------------------
    # Console helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pass_label() -> str:
        return colored("PASS", Colors.GREEN)

    @staticmethod
    def _fail_label() -> str:
        return colored("FAIL", Colors.RED)

    @staticmethod
    def _status_icon(passed: bool) -> str:
        return colored("PASS", Colors.GREEN) if passed else colored("FAIL", Colors.RED)

    # ------------------------------------------------------------------
    # print_summary
    # ------------------------------------------------------------------

    def print_summary(self, result: ScenarioResult) -> None:
        """Print concise pass/fail summary to console."""
        total = len(result.steps)
        passed = sum(1 for s in result.steps if s.passed)

        overall = self._pass_label() if result.overall_passed else self._fail_label()
        print(f"\n{colored('Scenario:', Colors.BOLD)} {result.scenario_id} ({result.test_mode})  {overall}")
        print(f"  Steps: {passed}/{total} passed")
        print(f"  Duration: {result.duration_s:.1f}s")

        p50 = f"{result.latency_p50_ms:.0f}ms" if result.latency_p50_ms is not None else "N/A"
        p95 = f"{result.latency_p95_ms:.0f}ms" if result.latency_p95_ms is not None else "N/A"
        print(f"  Latency P50: {p50} | P95: {p95}")

    # ------------------------------------------------------------------
    # print_detailed
    # ------------------------------------------------------------------

    def print_detailed(self, result: ScenarioResult) -> None:
        """Print step-by-step results with metrics tables."""
        self.print_summary(result)
        print()

        for step in result.steps:
            self._print_step(step)

    def _print_step(self, step: StepResult) -> None:
        user_text = step.user_input_text or "(no input)"
        agent_text = step.agent_response_text or "(no response)"
        block_match = step.expected_block == step.actual_block if step.expected_block else None
        block_icon = colored("PASS", Colors.GREEN) if block_match else colored("FAIL", Colors.RED) if block_match is not None else "?"

        print(f"  {colored(f'Step {step.step_number}:', Colors.BOLD)} User says \"{user_text}\"")
        print(f"    Agent response: \"{agent_text}\"")
        if step.expected_block:
            print(f"    Expected block: {step.expected_block} | Inferred: {step.actual_block or '?'}  {block_icon}")
        if step.latency_ms is not None:
            print(f"    Latency: {step.latency_ms:.0f}ms")

        if step.evaluations:
            self._print_eval_table(step.evaluations)
        if step.error:
            print(f"    {colored('Error:', Colors.RED)} {step.error}")
        print()

    def _print_eval_table(self, evaluations: dict[str, EvalResult]) -> None:
        """Print a formatted metrics table for a step."""
        # Compute column widths
        metric_w = max(len(m) for m in evaluations) if evaluations else 6
        metric_w = max(metric_w, 6)  # "Metric" header
        detail_w = 40

        header = f"    {'Metric':<{metric_w}}  {'Result':8}  Detail"
        sep = f"    {'-' * metric_w}  {'-' * 8}  {'-' * detail_w}"
        print(sep)
        print(header)
        print(sep)

        for metric, ev in evaluations.items():
            icon = self._status_icon(ev.passed)
            detail = ev.reasoning or ""
            print(f"    {metric:<{metric_w}}  {icon:8}  {detail}")
        print(sep)

    # ------------------------------------------------------------------
    # print_comparison
    # ------------------------------------------------------------------

    def print_comparison(
        self, text_result: ScenarioResult, e2e_result: ScenarioResult
    ) -> None:
        """Print side-by-side comparison of text sim vs E2E results."""
        print(f"\n{colored('Comparison:', Colors.BOLD)} {text_result.scenario_id}")
        print()

        # Header
        hdr = f"  {'Step':<6} {'Check':<20} {'Text Sim':10} {'E2E Voice':10} {'Diagnosis'}"
        sep = f"  {'-' * 6} {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 20}"
        print(sep)
        print(hdr)
        print(sep)

        max_steps = max(len(text_result.steps), len(e2e_result.steps))
        for i in range(max_steps):
            t_step = text_result.steps[i] if i < len(text_result.steps) else None
            e_step = e2e_result.steps[i] if i < len(e2e_result.steps) else None

            # Gather all metrics from both
            all_metrics: set[str] = set()
            if t_step:
                all_metrics.update(t_step.evaluations.keys())
            if e_step:
                all_metrics.update(e_step.evaluations.keys())

            for metric in sorted(all_metrics):
                t_passed = t_step.evaluations[metric].passed if (t_step and metric in t_step.evaluations) else None
                e_passed = e_step.evaluations[metric].passed if (e_step and metric in e_step.evaluations) else None

                t_label = self._status_icon(t_passed) if t_passed is not None else "N/A"
                e_label = self._status_icon(e_passed) if e_passed is not None else "N/A"

                diagnosis = self._diagnose(t_passed, e_passed)
                step_num = i + 1
                print(f"  {step_num:<6} {metric:<20} {t_label:10} {e_label:10} {diagnosis}")

        print(sep)

    @staticmethod
    def _diagnose(text_passed: bool | None, e2e_passed: bool | None) -> str:
        if text_passed is None or e2e_passed is None:
            return "—"
        if text_passed and e2e_passed:
            return "—"
        if text_passed and not e2e_passed:
            return colored("STT/TTS issue", Colors.YELLOW)
        if not text_passed and e2e_passed:
            return colored("Text-only anomaly", Colors.YELLOW)
        # both fail
        return colored("Prompt/flow issue", Colors.RED)

    # ------------------------------------------------------------------
    # generate_json_report
    # ------------------------------------------------------------------

    def generate_json_report(self, result: ScenarioResult) -> dict:
        """Generate structured JSON report."""
        total = len(result.steps)
        passed = sum(1 for s in result.steps if s.passed)

        steps_data = []
        for step in result.steps:
            evals = {}
            for metric, ev in step.evaluations.items():
                entry: dict = {"passed": ev.passed}
                if ev.score is not None:
                    entry["score"] = ev.score
                if ev.reasoning:
                    entry["reasoning"] = ev.reasoning
                if ev.details:
                    entry["details"] = ev.details
                evals[metric] = entry

            steps_data.append({
                "step": step.step_number,
                "user_input": step.user_input_text,
                "agent_response": step.agent_response_text,
                "expected_block": step.expected_block,
                "inferred_block": step.actual_block,
                "latency_ms": step.latency_ms,
                "evaluations": evals,
            })

        block_issues = self.generate_block_issue_map(result)

        return {
            "scenario_id": result.scenario_id,
            "test_mode": result.test_mode,
            "timestamp": result.started_at.isoformat(),
            "duration_s": result.duration_s,
            "overall_passed": result.overall_passed,
            "summary": {
                "total_steps": total,
                "passed_steps": passed,
                "failed_steps": total - passed,
                "latency_p50_ms": result.latency_p50_ms,
                "latency_p95_ms": result.latency_p95_ms,
            },
            "block_issues": block_issues,
            "steps": steps_data,
        }

    # ------------------------------------------------------------------
    # generate_block_issue_map
    # ------------------------------------------------------------------

    def generate_block_issue_map(self, result: ScenarioResult) -> dict:
        """Map failures to specific flow blocks with severity levels."""
        blocks: dict[str, dict] = {}

        for step in result.steps:
            block_id = step.expected_block or step.actual_block
            if not block_id:
                continue

            if block_id not in blocks:
                blocks[block_id] = {
                    "block_id": block_id,
                    "issues": [],
                    "_total": 0,
                    "_passed": 0,
                }

            for metric, ev in step.evaluations.items():
                blocks[block_id]["_total"] += 1
                if ev.passed:
                    blocks[block_id]["_passed"] += 1
                else:
                    blocks[block_id]["issues"].append({
                        "metric": metric,
                        "severity": _severity_for(metric),
                        "detail": ev.reasoning or "",
                        "step": step.step_number,
                    })

        # Compute pass_rate and remove internal counters
        for block in blocks.values():
            total = block.pop("_total")
            passed = block.pop("_passed")
            block["pass_rate"] = passed / total if total > 0 else 1.0

        return blocks

    # ------------------------------------------------------------------
    # aggregate_block_issues
    # ------------------------------------------------------------------

    def aggregate_block_issues(self, results: list[ScenarioResult]) -> dict:
        """Aggregate block issues across multiple scenario runs."""
        combined: dict[str, dict] = {}

        for result in results:
            single = self.generate_block_issue_map(result)
            for block_id, block_data in single.items():
                if block_id not in combined:
                    combined[block_id] = {
                        "block_id": block_id,
                        "issues": [],
                        "_rates": [],
                    }
                combined[block_id]["issues"].extend(block_data["issues"])
                combined[block_id]["_rates"].append(block_data["pass_rate"])

        # Average pass rates
        for block in combined.values():
            rates = block.pop("_rates")
            block["pass_rate"] = sum(rates) / len(rates) if rates else 1.0

        return combined

    # ------------------------------------------------------------------
    # save_report / save_comparison_report
    # ------------------------------------------------------------------

    def save_report(self, result: ScenarioResult, scenario_id: str) -> str:
        """Save full report to disk. Returns file path."""
        report = self.generate_json_report(result)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{scenario_id}_{ts}.json"
        path = Path(self.output_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        return str(path)

    def save_comparison_report(
        self, text_result: ScenarioResult, e2e_result: ScenarioResult, scenario_id: str
    ) -> str:
        """Save comparison report to disk. Returns file path."""
        text_report = self.generate_json_report(text_result)
        e2e_report = self.generate_json_report(e2e_result)

        # Build comparison rows
        comparisons = []
        max_steps = max(len(text_result.steps), len(e2e_result.steps))
        for i in range(max_steps):
            t_step = text_result.steps[i] if i < len(text_result.steps) else None
            e_step = e2e_result.steps[i] if i < len(e2e_result.steps) else None
            all_metrics: set[str] = set()
            if t_step:
                all_metrics.update(t_step.evaluations.keys())
            if e_step:
                all_metrics.update(e_step.evaluations.keys())

            for metric in sorted(all_metrics):
                t_passed = t_step.evaluations[metric].passed if (t_step and metric in t_step.evaluations) else None
                e_passed = e_step.evaluations[metric].passed if (e_step and metric in e_step.evaluations) else None
                diagnosis = self._diagnose_text(t_passed, e_passed)
                comparisons.append({
                    "step": i + 1,
                    "metric": metric,
                    "text_passed": t_passed,
                    "e2e_passed": e_passed,
                    "diagnosis": diagnosis,
                })

        report = {
            "scenario_id": scenario_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text_result": text_report,
            "e2e_result": e2e_report,
            "comparisons": comparisons,
        }

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{scenario_id}_comparison_{ts}.json"
        path = Path(self.output_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        return str(path)

    @staticmethod
    def _diagnose_text(text_passed: bool | None, e2e_passed: bool | None) -> str:
        """Plain-text diagnosis (no ANSI codes) for JSON output."""
        if text_passed is None or e2e_passed is None:
            return "unknown"
        if text_passed and e2e_passed:
            return "ok"
        if text_passed and not e2e_passed:
            return "STT/TTS issue"
        if not text_passed and e2e_passed:
            return "Text-only anomaly"
        return "Prompt/flow issue"
