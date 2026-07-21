"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from ir import builder


@pytest.fixture(autouse=True)
def _reset_id_counters():
    """Node IDs are a module-level counter; reset it before every test so
    assertions on exact IDs (e.g. ``fn_0001``) are deterministic
    regardless of test execution order.

    v2: the type-hole ID counter this fixture used to also reset
    (``typing_inference.infer.reset_hole_counter``) no longer exists --
    v2 has no type holes, so there's nothing left to reset there."""

    builder.reset_node_counter()
    yield
