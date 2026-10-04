"""The team's offline refine check, run under pytest so it is part of the same green/red signal."""

import refine_test


def test_refine_offline_scenarios():
    refine_test.main()
