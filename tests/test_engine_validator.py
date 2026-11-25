"""Engine, generator and validator tests. Offline. Run: python -m unittest discover -s tests"""
import json
import os
import random
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from quant import engine, generator, validator  # noqa: E402


def walk(n, seed=1, drift=0.0):
    rng, c, out = random.Random(seed), 100.0, []
    for i in range(n):
        o = c
        c = o * (1 + drift + rng.gauss(0, 0.01))
        out.append({"t": i * 3600, "o": o, "h": max(o, c) * 1.003, "l": min(o, c) * 0.997, "c": c})
    return out


class EngineTest(unittest.TestCase):
    def test_enters_next_open_and_stop_wins_a_tie(self):
        bars = [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
                {"t": 1, "o": 100, "h": 103, "l": 98, "c": 101},   # touches both: stop first
                {"t": 2, "o": 101, "h": 101, "l": 101, "c": 101}]
        t = engine.run(bars, [(1, 99, 102), None, None])
        self.assertEqual((t[0]["entry"], t[0]["exit"]), (100, 99))
        self.assertAlmostEqual(t[0]["r"], -1.0)

    def test_short_target_and_cost(self):
        bars = [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
                {"t": 1, "o": 100, "h": 100.5, "l": 97, "c": 98}]
        t = engine.run(bars, [(-1, 101, 98), None], cost=0.001)[0]
        self.assertEqual(t["exit"], 98)
        self.assertAlmostEqual(t["ret"], 0.02 - 0.001)
        self.assertAlmostEqual(t["r"], (0.02 - 0.001) / 0.01)

    def test_gap_through_stop_skips_the_trade(self):
        bars = [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
                {"t": 1, "o": 98, "h": 99, "l": 97, "c": 98}]
        self.assertEqual(engine.run(bars, [(1, 99, 105), None]), [])


class ValidatorTest(unittest.TestCase):
    def test_every_strategy_is_causal(self):
        bars = walk(1600)
        rng = random.Random(3)
        for st in generator.STRATEGIES:
            for p in st["grid"][:3]:
                self.assertTrue(validator.lookahead_ok(st["fn"], bars, p, 3, rng), st["name"])

    def test_lookahead_check_catches_a_cheat(self):
        def cheat(bars, p):   # peeks at the next close
            return [(1, b["c"] * 0.99, b["c"] * 1.02) if i + 1 < len(bars) and bars[i + 1]["c"] > b["c"] else None
                    for i, b in enumerate(bars)]
        self.assertFalse(validator.lookahead_ok(cheat, walk(400), {}, 5, random.Random(1)))

    def test_random_walk_is_not_approved(self):
        crit, _ = validator.load_criteria()
        crit = dict(crit, permutations=20)
        st = generator.STRATEGIES[1]   # donchian breakout
        m = validator.evaluate(st, "TEST", walk(6000, seed=7), 0.0006, crit)
        self.assertNotEqual(validator.verdict(m, crit)[0], "APPROVED")

    def test_verdict_tiers(self):
        crit, _ = validator.load_criteria()
        good = {"lookahead_ok": True, "oos_trades": 50, "oos_mean_r": 0.2, "lockbox_mean_r": 0.1, "lockbox_trades": 9,
                "costs_x2_mean_r": 0.1, "positive_folds": 3, "plateau_share": 0.7, "permutation_p": 0.02}
        self.assertEqual(validator.verdict(good, crit)[0], "APPROVED")
        self.assertEqual(validator.verdict(dict(good, permutation_p=0.4), crit)[0], "PROBATION")
        self.assertEqual(validator.verdict(dict(good, lockbox_mean_r=-0.1), crit)[0], "REJECTED")
        self.assertEqual(validator.verdict(dict(good, oos_trades=5), crit)[0], "NEEDS MORE EVIDENCE")
        self.assertEqual(validator.verdict(dict(good, lookahead_ok=False), crit)[0], "REJECTED")

    def test_log_is_hash_chained_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "v.jsonl")
            for i in range(3):
                validator.append({"n": i, "verdict": "REJECTED"}, path)
            self.assertTrue(validator.verify(path))
            lines = validator._lines(path)
            lines[1] = lines[1].replace("REJECTED", "APPROVED")      # someone edits a verdict
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self.assertFalse(validator.verify(path))

    def test_a_changed_strategy_is_a_new_version(self):
        st = dict(generator.STRATEGIES[0])
        v1 = generator.version(st)
        st["grid"] = st["grid"][:-1]
        self.assertNotEqual(v1, generator.version(st))

    def test_bot_refuses_a_pick_whose_code_changed(self):
        import bot
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "selection.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"pick": {"strategy": "rsi_reversion", "version": "not-the-current-one", "market": "CL=F",
                                    "params": {}, "verdict": "PROBATION", "risk_pct": 0.005}}, fh)
            saved, bot.SELECTION = bot.SELECTION, path
            try:
                self.assertEqual(list(bot.signals(0)), [])   # blocked before fetching any data
            finally:
                bot.SELECTION = saved

    def test_bot_scans_the_whole_ranking_not_just_the_first_open_market(self):
        import bot
        now = 10_000_000
        fresh = [dict(b, t=now - 3600 * (400 - i)) for i, b in enumerate(walk(400))]
        fake = {"CLOSED": walk(400), "SILENT": fresh, "FIRES": fresh}     # CLOSED's bars are years old
        fire = lambda bs, p: [None] * (len(bs) - 1) + [(-1, bs[-1]["c"] * 1.01, bs[-1]["c"] * 0.98)]
        silent = lambda bs, p: [None] * len(bs)
        stubs = [{"name": "fire", "fn": fire}, {"name": "silent", "fn": silent}]
        rank = [{"strategy": "fire", "version": "v", "market": "CLOSED", "params": {}, "verdict": "APPROVED"},
                {"strategy": "silent", "version": "v", "market": "SILENT", "params": {}, "verdict": "APPROVED"},
                {"strategy": "fire", "version": "v", "market": "FIRES", "params": {}, "verdict": "PROBATION"}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "selection.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"pick": rank[0], "ranking": rank}, fh)
            saved = bot.SELECTION, bot.bars, generator.STRATEGIES, generator.version
            bot.SELECTION, bot.bars = path, lambda sym, interval, rng: fake[sym]
            generator.STRATEGIES, generator.version = stubs, lambda st: "v"
            try:
                got = [p["market"] for p, _ in bot.signals(now)]
            finally:
                bot.SELECTION, bot.bars, generator.STRATEGIES, generator.version = saved
        self.assertEqual(got, ["FIRES"])

    def test_generator_cannot_touch_validator_state(self):
        src = open(os.path.join(ROOT, "quant", "generator.py"), encoding="utf-8").read()
        self.assertIsNone(re.search(r"validat|criteria|selection\.json", src.split('"""', 2)[2]))


if __name__ == "__main__":
    unittest.main()
