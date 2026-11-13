"""Offline tests for the phase-1 research store. Run: python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quant import db, data  # noqa: E402


def bar(t, o=10, h=11, l=9, c=10.5, v=100):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")

    def test_upsert_is_idempotent_and_revises_the_last_bar(self):
        db.upsert_bars(self.con, "X", "1h", [bar(0), bar(3600)])
        db.upsert_bars(self.con, "X", "1h", [bar(3600, c=10.9)])          # Yahoo revises the live bar
        rows = db.load_bars(self.con, "X", "1h")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["c"], 10.9)
        self.assertEqual(db.last_ts(self.con, "X", "1h"), 3600)
        self.assertIsNone(db.last_ts(self.con, "X", "5m"))

    def test_quality_flags_bad_bars_and_intraday_holes(self):
        rows = [bar(0), bar(300), bar(300 * 10), bar(300 * 11, h=10)]     # hole of 9 bars; high under close
        q = data.check(rows, "5m")
        self.assertEqual(q["bad_bars"], 1)
        self.assertEqual(q["gaps"], 1)
        overnight = [bar(0), bar(17 * 3600)]                                # session break, not a hole
        self.assertEqual(data.check(overnight, "5m")["gaps"], 0)

    def test_ledger_import_pairs_round_trips_once(self):
        events = [
            {"event": "open", "sym": "MSFT", "market": "stock", "qty": 0.2, "entry": 521.9, "stop": 517.36,
             "target": 530.98, "why": "orb", "at": "2026-11-13 14:56:53Z"},
            {"event": "open", "sym": "ETH-USD", "market": "crypto_short", "side": -1, "qty": 0.01, "entry": 1900,
             "stop": 1950, "target": 1825, "why": "1h breakdown", "at": "2026-11-13 22:00:00Z"},
            {"event": "close", "sym": "MSFT", "exit": 524.1, "why": "flat by close", "pnl": 0.4544,
             "at": "2026-11-13 20:55:57Z"},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write("\n".join(json.dumps(e) for e in events) + "\n")
        try:
            self.assertEqual(db.import_ledger(self.con, fh.name), 1)       # ETH still open: not imported
            self.assertEqual(db.import_ledger(self.con, fh.name), 0)       # second run adds nothing
        finally:
            os.remove(fh.name)
        row = self.con.execute("SELECT symbol, side, pnl, exit_reason FROM trades").fetchone()
        self.assertEqual(row, ("MSFT", 1, 0.4544, "flat by close"))


if __name__ == "__main__":
    unittest.main()
