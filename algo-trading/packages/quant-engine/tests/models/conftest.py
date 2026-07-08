"""
tests/models/conftest.py — Pytest configuration for model tests.

macOS OpenMP conflict
---------------------
LightGBM uses libomp.dylib; PyTorch (used by LSTM, Transformer, GP, and
stable-baselines3) ships its own OpenMP runtime.  When both are loaded in the
same process on macOS arm64, a segfault occurs.

Solution: mark LightGBM tests with ``@pytest.mark.forked`` so pytest-forked
runs them in an isolated subprocess.  All other model tests run in the main
process as normal.
"""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "forked: run test in a separate forked subprocess (avoids OpenMP conflicts)",
    )
