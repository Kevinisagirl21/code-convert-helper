"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from ir import builder
from typing_inference import infer


@pytest.fixture(autouse=True)
def _reset_id_counters():
    """Type-hole and node IDs are module-level counters; reset them before
    every test so assertions on exact IDs (e.g. ``hole_0001``) are
    deterministic regardless of test execution order."""

    infer.reset_hole_counter()
    builder.reset_node_counter()
    yield
