"""Spec 3.4 evidence requirement: "a PR with a deliberately failing test, screenshot of the red
check and the blocked merge button, fixed in the same PR, screenshot of green."

This test is deliberately wrong in its first commit on this PR, to prove main's branch
protection (PR required, 1 approval, all 7 ci.yml checks required) actually blocks a merge when
a check is red - not just that the checks exist. The second commit on this same PR fixes it.
"""


def test_ci_gate_demo() -> None:
    assert 1 + 1 == 2  # fixed
