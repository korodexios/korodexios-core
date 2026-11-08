"""stop_timeout is the budget of the whole shutdown (review 2026-11-08, R2) and a stop request
halts module start-up (R3)."""

from __future__ import annotations

import time

from korodexios_core.kernel.contract import ModuleState
from korodexios_core.kernel.registry import ModuleRegistry
from korodexios_core.kernel.shutdown import MIN_STEP_SECONDS, Budget

from .conftest import ScriptedModule
from .test_registry import DummyLogger, make_spec


def test_a_budget_hands_out_what_is_left() -> None:
    budget = Budget(0.4)
    first = budget.left()
    time.sleep(0.2)
    assert 0.1 < budget.left() < first
    time.sleep(0.35)
    assert budget.left() == MIN_STEP_SECONDS, "a late step still gets a moment"
    assert Budget(None).left() == 0.0, "unbounded stays unbounded"


def test_slow_modules_share_one_stop_budget() -> None:
    registry = ModuleRegistry(DummyLogger())
    for name in ("one", "two", "three"):
        registry.register(ScriptedModule(name, stop_delay=1.5), make_spec(name))
    registry.initialize_all(object())
    registry.start_all()
    started = time.monotonic()
    registry.stop_all(timeout=0.5)
    elapsed = time.monotonic() - started
    assert elapsed < 1.2, f"three 1.5 s stops under a 0.5 s budget took {elapsed:.1f}s"


def test_start_up_halts_when_a_stop_was_requested() -> None:
    registry = ModuleRegistry(DummyLogger())
    first, second = ScriptedModule("first"), ScriptedModule("second")
    registry.register(first, make_spec("first"))
    registry.register(second, make_spec("second"))
    registry.initialize_all(object())
    registry.start_all(halt=lambda: "start" in first.calls)
    assert registry.get("first").state is ModuleState.RUNNING
    assert "start" not in second.calls
    assert registry.get("second").state is ModuleState.INITIALIZED
