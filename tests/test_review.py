"""Tests for the 2-hourly review's protective rules. Run: python -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review  # noqa: E402


def trade(r, market="stock"):
    # entry 100, stop 99, qty 1: risk 1 USD, so pnl in USD equals R
    return {"sym": "X", "market": market, "qty": 1, "entry": 100, "stop": 99, "pnl": r, "opened": "2026-11-16 10:00 ET"}


def decide(rs, overrides=None):
    s = review.breakdown([trade(r) for r in rs], lambda t: t["market"])
    return review.decide(s, overrides or {})


class ReviewTest(unittest.TestCase):
    def test_r_multiple_handles_shorts(self):
        short = {"qty": 2, "entry": 100, "stop": 101, "pnl": 3}
        self.assertEqual(review.r_multiple(short), 1.5)

    def test_few_trades_change_nothing(self):
        new, actions = decide([-1, -1, 2, -1])
        self.assertEqual((new["paused"], actions), ([], []))

    def test_losing_streak_pauses(self):
        new, _ = decide([2] + [-1] * 6)
        self.assertEqual(new["paused"], ["stock"])

    def test_deep_drawdown_pauses_after_enough_trades(self):
        new, _ = decide([-1, -1, 2, -1, -1, -1, -1, -1, 2, -1, -1])   # -6R over 11, streak 2
        self.assertEqual(new["paused"], ["stock"])

    def test_mildly_negative_halves_risk_then_restores(self):
        new, _ = decide([-1, 2, -1, -1, 2, -1, -1, -1])                 # -2R over 8
        self.assertEqual((new["paused"], new["risk_scale"]), ([], {"stock": 0.5}))
        again, actions = decide([-1, 2, -1, -1, 2, -1, -1, -1, 2, 2], new)
        self.assertEqual(again["risk_scale"], {"stock": 1.0})
        self.assertTrue(actions[0].startswith("RESTORED"))

    def test_pauses_are_sticky(self):
        new, actions = decide([2] * 10, {"paused": ["stock"]})
        self.assertEqual((new["paused"], actions), (["stock"], []))


if __name__ == "__main__":
    unittest.main()
