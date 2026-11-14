"""Independent Strategy Validator. The only module allowed to approve, put on probation or reject.

  python -m quant.validator run [--strategy NAME] [--market SYM]   # validate, append verdicts
  python -m quant.validator select                                 # rank and pick what the bot trades
  python -m quant.validator verify                                 # check the hash chain of the log

Independence, in practice:
  * criteria come from validation/criteria.json (frozen before evaluating); each verdict stores its hash;
  * the validator re-runs every backtest itself with its own cost model; the generator supplies only rules;
  * the newest 15% of the data (the lockbox) is never used to choose parameters;
  * verdicts are appended to validation/validations.jsonl, each line chained to the previous one's hash;
  * any error while validating is recorded as REJECTED (blocked), never as a pass;
  * a verdict belongs to one strategy version: a changed strategy has a new version and no verdict.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random

from . import db, engine, generator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRITERIA = os.path.join(ROOT, "validation", "criteria.json")
LOG = os.path.join(ROOT, "validation", "validations.jsonl")
SELECTION = os.path.join(ROOT, "validation", "selection.json")


def load_criteria():
    with open(CRITERIA, "rb") as fh:
        raw = fh.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------- the append-only log

def _lines(path=None):
    path = path or LOG
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines() if ln.strip()]


def append(record, path=None):
    lines = _lines(path)
    record["prev"] = hashlib.sha256(lines[-1].encode()).hexdigest() if lines else "genesis"
    with open(path or LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def verify(path=None):
    """True when every line points at the hash of the line before it (no edits, no deletions)."""
    lines = _lines(path)
    prev = "genesis"
    for ln in lines:
        if json.loads(ln).get("prev") != prev:
            return False
        prev = hashlib.sha256(ln.encode()).hexdigest()
    return True


# ---------------------------------------------------------------- checks

def lookahead_ok(fn, bars, params, checks, rng):
    """Signals computed on truncated data must equal the full-data signals up to the cut."""
    full = fn(bars, params)
    for _ in range(checks):
        k = rng.randrange(len(bars) // 2, len(bars))
        if fn(bars[:k], params) != full[:k]:
            return False
    return True


def permuted(bars, rng):
    """Same bar shapes and the same returns in random order: any structure in time is destroyed."""
    rets = [bars[i]["c"] / bars[i - 1]["c"] for i in range(1, len(bars))]
    rng.shuffle(rets)
    out, c = [dict(bars[0])], bars[0]["c"]
    for b, r in zip(bars[1:], rets):
        c *= r
        k = c / b["c"]
        out.append({"t": b["t"], "o": b["o"] * k, "h": b["h"] * k, "l": b["l"] * k, "c": c})
    return out


def best_param(sigs_by_p, bars, cost, start, end, min_trades):
    best = None
    for key, sigs in sigs_by_p.items():
        s = engine.summary(engine.run(bars, sigs, cost, start=start, end=end))
        if s["n"] >= min_trades and (best is None or s["sum_r"] > best[1]):
            best = (key, s["sum_r"])
    return best[0] if best else None


def evaluate(strategy, market, bars, cost, crit, seed=19):
    rng = random.Random(seed)
    grid = strategy["grid"]
    fn = strategy["fn"]
    keys = [json.dumps(p, sort_keys=True) for p in grid]
    sigs = {k: fn(bars, p) for k, p in zip(keys, grid)}
    m = {"bars": len(bars)}

    m["lookahead_ok"] = lookahead_ok(fn, bars, grid[0], crit["lookahead_checks"], rng)
    lock = int(len(bars) * (1 - crit["lockbox_fraction"]))
    size = lock // crit["walk_forward_folds"]

    oos, oos2, folds = [], [], []
    for f in range(1, crit["walk_forward_folds"]):
        a, b = f * size, (f + 1) * size
        key = best_param(sigs, bars, cost, 0, a, crit["min_is_trades"])
        if key is None:
            folds.append(None)
            continue
        t = engine.run(bars, sigs[key], cost, start=a, end=b)
        oos += t
        oos2 += engine.run(bars, sigs[key], 2 * cost, start=a, end=b)
        folds.append(engine.summary(t)["sum_r"])
    final = best_param(sigs, bars, cost, 0, lock, crit["min_is_trades"])
    m["chosen_params"] = json.loads(final) if final else None
    s_oos, s2 = engine.summary(oos), engine.summary(oos2)
    lock_s = engine.summary(engine.run(bars, sigs[final], cost, start=lock)) if final else engine.summary([])
    m.update({"oos_trades": s_oos["n"], "oos_mean_r": s_oos["mean_r"], "oos_win": s_oos["win"], "oos_ret": s_oos["ret"],
              "costs_x2_mean_r": s2["mean_r"], "lockbox_trades": lock_s["n"], "lockbox_mean_r": lock_s["mean_r"],
              "lockbox_ret": lock_s["ret"], "positive_folds": sum(1 for x in folds if x and x > 0),
              "fold_sum_r": folds})
    dev = [engine.summary(engine.run(bars, s, cost, end=lock))["sum_r"] for s in sigs.values()]
    m["plateau_share"] = sum(x > 0 for x in dev) / len(dev)
    m["buy_hold_oos"] = bars[lock - 1]["c"] / bars[size]["c"] - 1
    m["buy_hold_lockbox"] = bars[-1]["c"] / bars[lock]["c"] - 1

    m["permutation_p"] = None
    if final and s_oos["mean_r"] > 0:   # only worth the time when there is something to explain
        real = engine.summary(engine.run(bars, sigs[final], cost, end=lock))["sum_r"]
        dev_bars, better = bars[:lock], 0
        for _ in range(crit["permutations"]):
            pb = permuted(dev_bars, rng)
            if engine.summary(engine.run(pb, fn(pb, json.loads(final)), cost))["sum_r"] >= real:
                better += 1
        m["permutation_p"] = (better + 1) / (crit["permutations"] + 1)
    return m


def verdict(m, crit):
    reasons = []
    if not m["lookahead_ok"]:
        return "REJECTED", ["look-ahead: signals change when future bars are removed"]
    if m["oos_trades"] < crit["min_oos_trades"]:
        return "NEEDS MORE EVIDENCE", ["%d out-of-sample trades, %d needed" % (m["oos_trades"], crit["min_oos_trades"])]
    checks = [
        (m["oos_mean_r"] >= crit["min_oos_mean_r"], "out-of-sample mean %.3fR (min %.2f)" % (m["oos_mean_r"], crit["min_oos_mean_r"])),
        (m["lockbox_mean_r"] > crit["min_lockbox_mean_r"], "lockbox mean %.3fR over %d trades" % (m["lockbox_mean_r"], m["lockbox_trades"])),
        (m["costs_x2_mean_r"] > crit["min_costs_x2_mean_r"], "at twice the costs %.3fR" % m["costs_x2_mean_r"]),
        (m["positive_folds"] >= crit["min_positive_folds"], "%d positive walk-forward folds" % m["positive_folds"]),
        (m["plateau_share"] >= crit["min_plateau_share"], "%.0f%% of the parameter grid profitable" % (100 * m["plateau_share"])),
        (m["permutation_p"] is not None and m["permutation_p"] <= crit["max_permutation_p"],
         "permutation p = %s" % ("n/a" if m["permutation_p"] is None else "%.3f" % m["permutation_p"])),
    ]
    failed = [why for ok, why in checks if not ok]
    if not failed:
        return "APPROVED", ["all checks passed: " + "; ".join(why for _, why in checks)]
    if m["oos_mean_r"] > 0 and m["lockbox_mean_r"] > 0:
        return "PROBATION", ["profitable out of sample and in the lockbox after costs, but failed: " + "; ".join(failed)]
    return "REJECTED", ["failed: " + "; ".join(failed)]


def run(names=None, markets=None, log=print):
    crit, crit_hash = load_criteria()
    con = db.connect()
    market_class = dict(con.execute("SELECT symbol, market FROM instruments"))
    for st in generator.STRATEGIES:
        if names and st["name"] not in names:
            continue
        ver = generator.version(st)
        for sym in st["markets"]:
            if markets and sym not in markets:
                continue
            rec = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "strategy": st["name"],
                   "family": st["family"], "version": ver, "market": sym, "interval": st["interval"],
                   "hypothesis": st["hypothesis"], "criteria_hash": crit_hash}
            try:
                bars = db.load_bars(con, sym, st["interval"])
                if len(bars) < 2000:
                    raise ValueError("only %d bars" % len(bars))
                q = con.execute("SELECT bad_bars, gaps FROM quality WHERE symbol=? AND interval=?", (sym, st["interval"])).fetchone()
                rec["data"] = {"source": "Yahoo Finance v8 chart", "bars": len(bars), "first": bars[0]["t"], "last": bars[-1]["t"],
                               "bad_bars": q[0] if q else None, "gaps": q[1] if q else None,
                               "hash": hashlib.sha256(json.dumps([round(b["c"], 6) for b in bars]).encode()).hexdigest()[:16]}
                cost = engine.COSTS.get(market_class.get(sym, "stock"), 0.001)
                rec["cost_round_trip"] = cost
                rec["metrics"] = evaluate(st, sym, bars, cost, crit)
                rec["verdict"], rec["reasons"] = verdict(rec["metrics"], crit)
            except Exception as e:   # a validator that cannot finish blocks the strategy
                rec["verdict"], rec["reasons"] = "REJECTED", ["validator error: %s" % e]
            append(rec)
            mm = rec.get("metrics", {})
            log("%-18s %-9s %-19s oos %3s trades %+.3fR  lockbox %+.3fR  p=%s" % (
                st["name"], sym, rec["verdict"], mm.get("oos_trades", "-"), mm.get("oos_mean_r", 0), mm.get("lockbox_mean_r", 0),
                mm.get("permutation_p")))


def select(write=True):
    """Latest verdict per (strategy, market) for the CURRENT strategy versions and criteria; rank the
    tradeable ones by out-of-sample return after costs; the top one is what the bot trades."""
    crit, crit_hash = load_criteria()
    current = {st["name"]: generator.version(st) for st in generator.STRATEGIES}
    latest = {}
    for ln in _lines():
        r = json.loads(ln)
        if current.get(r["strategy"]) == r["version"] and r["criteria_hash"] == crit_hash:
            latest[(r["strategy"], r["market"])] = r
    ok = [r for r in latest.values() if r["verdict"] in ("APPROVED", "PROBATION") and r["metrics"]["oos_ret"] > 0]
    ok.sort(key=lambda r: r["metrics"]["oos_ret"], reverse=True)
    ranking = [{"strategy": r["strategy"], "version": r["version"], "market": r["market"], "verdict": r["verdict"],
                "risk_pct": crit["risk"][r["verdict"]], "params": r["metrics"]["chosen_params"],
                "oos_ret": r["metrics"]["oos_ret"], "oos_mean_r": r["metrics"]["oos_mean_r"],
                "lockbox_mean_r": r["metrics"]["lockbox_mean_r"], "judged_at": r["at"]} for r in ok]
    out = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "criteria_hash": crit_hash,
           "log_intact": verify(), "pick": ranking[0] if ranking and verify() else None, "ranking": ranking}
    if write:
        with open(SELECTION, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "select", "verify"])
    ap.add_argument("--strategy", action="append")
    ap.add_argument("--market", action="append")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.strategy, a.market)
    elif a.cmd == "select":
        s = select()
        print(json.dumps({"pick": s["pick"], "candidates": len(s["ranking"]), "log_intact": s["log_intact"]}, indent=2))
    else:
        print("log intact" if verify() else "LOG TAMPERED: hash chain broken")


if __name__ == "__main__":
    main()
