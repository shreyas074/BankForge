"""
Stdlib-only test runner — no pytest required.

Every file under tests/ is written in standard pytest style (plain
functions named test_*, bare `assert` statements, no custom fixtures).
That means they run unmodified under real `pytest tests/` once you have
network access to install it (per brief §2/§6) — this script exists only
to produce genuine, executed-in-this-sandbox pass/fail evidence where
pytest itself isn't installable.

Usage:
    python3 run_tests.py
"""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent / "tests"


def discover_test_modules() -> list[str]:
    modules = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        modules.append(f"tests.{path.stem}")
    return modules


def run() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    passed, failed, errors = 0, 0, []

    for mod_name in discover_test_modules():
        module = importlib.import_module(mod_name)
        test_funcs = [
            getattr(module, name) for name in dir(module)
            if name.startswith("test_") and callable(getattr(module, name))
        ]
        for fn in test_funcs:
            full_name = f"{mod_name}.{fn.__name__}"
            try:
                fn()
                passed += 1
                print(f"PASS  {full_name}")
            except AssertionError as e:
                failed += 1
                errors.append((full_name, "AssertionError", str(e)))
                print(f"FAIL  {full_name}: {e}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                tb = traceback.format_exc(limit=3)
                errors.append((full_name, type(e).__name__, str(e)))
                print(f"ERROR {full_name}: {type(e).__name__}: {e}")

    total = passed + failed
    print("\n" + "=" * 70)
    print(f"{passed}/{total} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for name, kind, msg in errors:
            print(f"  - {name} [{kind}]: {msg}")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
