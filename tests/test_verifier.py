"""Verification compares state, not responses."""

from __future__ import annotations

import pytest

from agent_approval_patterns.verifier import FAILED, NOT_CHECKABLE, UNCONFIRMED, VERIFIED, classify


@pytest.mark.parametrize("before, after, expected, outcome", [
    ("RELEASE_FAILED", "RELEASED", "RELEASED", VERIFIED),
    ("RELEASE_FAILED", "ON_HOLD", "RELEASED", FAILED),
    ("RELEASE_FAILED", "RELEASE_FAILED", "RELEASED", UNCONFIRMED),
    ("RELEASE_FAILED", None, "RELEASED", NOT_CHECKABLE),
    ("RELEASE_FAILED", "RELEASED", None, NOT_CHECKABLE),
])
def test_classify(before, after, expected, outcome):
    assert classify(before, after, expected) == outcome
