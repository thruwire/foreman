"""Real end-to-end run: Hermes worker + Jev supervisor on the scratch repo."""
import asyncio
import sys
from pathlib import Path

# Redirected stdout on Windows defaults to cp1252; hermes output carries emoji.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=False)
load_dotenv(override=False)

from foreman.config import FactoryConfig
from foreman.foreman import JevForemanModel
from foreman.persistence import RunStore
from foreman.runtime import FactoryRuntime

REPO = Path(r"C:\Users\tjarman\repos\foreman-scratch")
JOB = (
    "Add a `subtract(a, b)` function to calc.py and a test file test_calc.py "
    "that covers both add and subtract. Run the tests to prove they pass. "
    "Keep changes minimal."
)


async def main() -> None:
    config = FactoryConfig(
        worker_backend="hermes",
        hermes_max_turns=30,
        worker_timeout_seconds=900,
        overall_timeout_seconds=1200,
        max_workers=6,
        use_smoothed_scores=True,
        score_smoothing_alpha=0.4,
        verify_tests_on_complete=True,
        cold_start_grace_seconds=30.0,
        defer_human_while_progressing=True,
        human_deferral_liveness_seconds=240,
        stuck_requires_silence_seconds=240,
        judgment_grace_seconds=300,
        # 20 iterations x ~15s assessments capped a factory at ~5 min, far
        # below overall_timeout; real-sized chunks need the headroom.
        max_iterations=80,
        implementation_for_verification_threshold=0.45,
        finish_threshold=0.35,
        requirements_threshold=0.35,
        tests_threshold=0.30,
        assessment_min_interval_seconds=15,
        max_retries=1,
        periodic_assessment_seconds=20,
    )
    store = RunStore(REPO)
    runtime = FactoryRuntime(
        repository=REPO,
        job=JOB,
        model=JevForemanModel(timeout_seconds=config.jev_timeout_seconds),
        config=config,
        store=store,
        event_sink=lambda event: print(
            f"[event] {event.event_type.value}: {str(event.payload)[:200]}", flush=True
        ),
    )

    state = await runtime.run()
    print("\n=== FINAL ===")
    print("status:", state.status.value)
    print("workers:", len(state.workers), "| retries:", state.retry_count)
    for w in state.workers:
        print(f"  {w.worker_id} {w.worker_type.value} -> {w.status.value}")
    print("run dir:", store.run_dir(state.run_id))


if __name__ == "__main__":
    asyncio.run(main())