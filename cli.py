"""voiceaiqa CLI — run test scenarios against voice agents.

Usage:
  python cli.py run --scenario scenarios/booking_happy_path.yaml --mock
  python cli.py run --scenario-dir scenarios/ --mock
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build_runner(mock: bool = False):
    """Build a ScenarioRunner with all dependencies wired up."""
    from config import settings
    from core.audio_gen import AudioGenerator
    from core.scenario_runner import ScenarioRunner
    from core.vad import TurnDetector
    from reco.client import RecoClient

    # RecoClient
    use_mock = mock or settings.RECO_MOCK_MODE
    reco_client = RecoClient(
        base_url=settings.RECO_API_URL,
        token=settings.RECO_API_TOKEN,
        mock=use_mock,
    )

    # AudioGenerator
    tts_provider = "mock" if use_mock else "cartesia"
    audio_gen = AudioGenerator(tts_provider=tts_provider)

    # TurnDetector — use a mock VAD model in mock mode
    if use_mock:
        turn_detector = TurnDetector(
            silence_threshold_ms=1500,
            min_speech_ms=300,
            vad_model=_mock_vad_model(),
        )
    else:
        turn_detector = TurnDetector(silence_threshold_ms=1500, min_speech_ms=300)

    # CallReceiver
    if use_mock:
        from receivers.mock_receiver import MockReceiver

        receiver = MockReceiver(
            agent_speech_ms=500,
            agent_silence_ms=2000,
            num_agent_turns=10,
        )
    else:
        from receivers.twilio_receiver import TwilioReceiver

        receiver = TwilioReceiver()

    return ScenarioRunner(
        reco_client=reco_client,
        receiver=receiver,
        turn_detector=turn_detector,
        audio_generator=audio_gen,
    )


def _mock_vad_model():
    """Return a simple callable that acts as a VAD model.

    Uses RMS energy to decide speech vs silence. Handles both torch
    tensors (normalized to [-1,1]) and plain lists (raw PCM16 values).
    """

    def vad_fn(samples, sample_rate):
        try:
            import torch
            if isinstance(samples, torch.Tensor):
                if samples.numel() == 0:
                    return 0.0
                rms = samples.pow(2).mean().sqrt().item()
                return 0.9 if rms > 0.015 else 0.1
        except ImportError:
            pass

        if not samples:
            return 0.0
        if isinstance(samples, list):
            rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        else:
            rms = 0.0
        return 0.9 if rms > 500 else 0.1

    return vad_fn


def load_scenarios(scenario_path: str | None, scenario_dir: str | None) -> list:
    """Load TestScenario(s) from a file or directory."""
    from models.scenario import TestScenario

    scenarios = []

    if scenario_path:
        path = Path(scenario_path)
        if not path.exists():
            print(f"Error: scenario file not found: {path}", file=sys.stderr)
            sys.exit(1)
        scenarios.append(TestScenario.from_yaml(path))

    if scenario_dir:
        dir_path = Path(scenario_dir)
        if not dir_path.is_dir():
            print(f"Error: scenario directory not found: {dir_path}", file=sys.stderr)
            sys.exit(1)
        yaml_files = sorted(dir_path.glob("*.yaml")) + sorted(dir_path.glob("*.yml"))
        if not yaml_files:
            print(f"Warning: no YAML files found in {dir_path}", file=sys.stderr)
        for yf in yaml_files:
            scenarios.append(TestScenario.from_yaml(yf))

    return scenarios


def print_result(result) -> None:
    """Print a summary of a ScenarioResult."""
    print(f"\n{'=' * 60}")
    print(f"Scenario: {result.scenario_id}")
    print(f"Mode: {result.test_mode}")
    print(f"Duration: {result.duration_s:.1f}s")
    if result.error:
        print(f"ERROR: {result.error}")
    print(f"Steps completed: {len(result.steps)}")

    for step in result.steps:
        status = "OK" if step.error is None else f"ERROR: {step.error}"
        latency_str = f"{step.latency_ms:.0f}ms" if step.latency_ms else "n/a"
        print(f"  Step {step.step_number}: {status} | "
              f"agent_audio={step.agent_audio_duration_ms:.0f}ms | "
              f"latency={latency_str}")

    if result.latency_p50_ms is not None:
        print(f"Latency P50: {result.latency_p50_ms:.0f}ms")
    if result.latency_p95_ms is not None:
        print(f"Latency P95: {result.latency_p95_ms:.0f}ms")
    print(f"{'=' * 60}")


async def run_command(args: argparse.Namespace) -> int:
    """Execute the 'run' subcommand."""
    scenarios = load_scenarios(args.scenario, args.scenario_dir)
    if not scenarios:
        print("Error: no scenarios specified. Use --scenario or --scenario-dir",
              file=sys.stderr)
        return 1

    runner = build_runner(mock=args.mock)

    exit_code = 0
    for scenario in scenarios:
        print(f"\nRunning scenario: {scenario.scenario_id}")
        result = await runner.run_scenario(scenario)
        print_result(result)

        if result.error:
            exit_code = 1

    # Cleanup
    await runner.reco_client.close()

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="voiceaiqa - Voice Agent QA Tool",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run test scenario(s)")
    run_parser.add_argument("--scenario", type=str,
                            help="Path to a scenario YAML file")
    run_parser.add_argument("--scenario-dir", type=str,
                            help="Directory containing scenario YAML files")
    run_parser.add_argument("--mock", action="store_true",
                            help="Use mock mode (no real calls or APIs)")

    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    if args.command == "run":
        exit_code = asyncio.run(run_command(args))
        sys.exit(exit_code)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
