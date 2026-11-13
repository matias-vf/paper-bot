#!/usr/bin/env python3
"""Review the bot's closed trades every 5 trades: what went well, what went badly, what to change.

  python review.py                    # review now: writes review.md and overrides.json
  python review.py --every-trades 5   # only if 5 more trades closed since the last review (the bot runs this)

What it changes on its own is deliberately limited. Tuning parameters on a handful of live trades
would fit noise (see the overfitting lessons), so the only automatic actions are protective:

  * pause a strategy after MAX_LOSING_STREAK losses in a row, or once it has at least MIN_TRADES
    trades and is down PAUSE_R or more in total (in units of R, the risk taken per trade);
  * halve its risk while it is negative after MIN_TRADES trades;
  * a paused strategy stays paused until a person removes it from overrides.json.

Everything else (which exits work, which hours, which symbols) is reported as evidence, with the
sample size next to it, for a person to judge.
"""
import datetime as dt
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
OVERRIDES = os.path.join(HERE, "overrides.json")
REVIEW = os.path.join(HERE, "review.md")

MIN_TRADES = 8
PAUSE_R = -4.0
MAX_LOSING_STREAK = 6


def due(n_closed, reviewed_trades, every):
    """A review is due once `every` more trades have closed since the last one."""
    return n_closed - reviewed_trades >= every


def exit_reason(t):
    # older records stored the exit reason in "why", overwriting the entry reason
    return t.get("exit_why", t.get("why", "?"))


def r_multiple(t):
    risk = t["qty"] * abs(t["entry"] - t["stop"])
    return t["pnl"] / risk if risk > 0 else 0.0


def stats(trades):
    rs = [r_multiple(t) for t in trades]
    streak = worst = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    return {"n": len(rs), "wins": sum(r > 0 for r in rs), "sum_r": sum(rs), "avg_r": sum(rs) / len(rs) if rs else 0.0,
            "pnl": sum(t["pnl"] for t in trades), "streak_now": streak, "worst_streak": worst}


def decide(by_strategy, overrides):
    """Return new overrides and the list of actions taken. Pauses are sticky."""
    paused = set(overrides.get("paused", []))
    risk_scale = dict(overrides.get("risk_scale", {}))
    actions = []
    for name, s in by_strategy.items():
        if name in paused:
            continue
        if s["streak_now"] >= MAX_LOSING_STREAK:
            paused.add(name)
            actions.append("PAUSED %s: %d losses in a row" % (name, s["streak_now"]))
        elif s["n"] >= MIN_TRADES and s["sum_r"] <= PAUSE_R:
            paused.add(name)
            actions.append("PAUSED %s: %.1fR over %d trades" % (name, s["sum_r"], s["n"]))
        elif s["n"] >= MIN_TRADES and s["avg_r"] < 0 and risk_scale.get(name, 1.0) == 1.0:
            risk_scale[name] = 0.5
            actions.append("HALVED risk on %s: average %.2fR over %d trades" % (name, s["avg_r"], s["n"]))
        elif s["n"] >= MIN_TRADES and s["avg_r"] > 0 and risk_scale.get(name, 1.0) < 1.0:
            risk_scale[name] = 1.0
            actions.append("RESTORED risk on %s: average back to %.2fR" % (name, s["avg_r"]))
    return {"paused": sorted(paused), "risk_scale": risk_scale}, actions


def breakdown(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: stats(v) for k, v in sorted(groups.items())}


def table(title, groups):
    lines = ["### " + title, "", "| | trades | won | total R | avg R | P&L USD |", "| --- | --- | --- | --- | --- | --- |"]
    for k, s in groups.items():
        lines.append("| %s | %d | %d | %+.2f | %+.2f | %+.2f |" % (k, s["n"], s["wins"], s["sum_r"], s["avg_r"], s["pnl"]))
    return lines + [""]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--every-trades", type=int, metavar="N",
                    help="run only once N more trades have closed since the last review")
    a = ap.parse_args()
    with open(STATE, encoding="utf-8") as fh:
        st = json.load(fh)
    overrides = {}
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, encoding="utf-8") as fh:
            overrides = json.load(fh)
    closed = st.get("closed", [])
    if a.every_trades and not due(len(closed), overrides.get("reviewed_trades", 0), a.every_trades):
        print("%d closed trades, last review at %d: next review at %d" % (
            len(closed), overrides.get("reviewed_trades", 0), overrides.get("reviewed_trades", 0) + a.every_trades))
        return
    by_strategy = breakdown(closed, lambda t: t.get("market", "?"))
    new, actions = decide(by_strategy, overrides)
    new["reviewed_trades"] = len(closed)
    new["reviewed_at"] = int(dt.datetime.now(dt.timezone.utc).timestamp())
    with open(OVERRIDES, "w", encoding="utf-8") as fh:
        json.dump(new, fh, indent=2)

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# Bot review", "", "Generated %s from %d closed trades. Equity %.2f USD (start 110.00)." % (
        now, len(closed), st.get("equity", st["cash"])), ""]
    if len(closed) < MIN_TRADES:
        lines += ["**Too few trades to judge anything yet** (%d of the %d needed). The numbers below are a log, "
                  "not evidence." % (len(closed), MIN_TRADES), ""]
    lines += ["## Actions taken", ""] + (["- " + a for a in actions] or ["- none"]) + [""]
    lines += ["Paused strategies: %s. Risk scale: %s." % (", ".join(new["paused"]) or "none",
                                                        json.dumps(new["risk_scale"]) if new["risk_scale"] else "normal"), ""]
    lines += ["## What went well and what went badly", ""]
    lines += table("By strategy", by_strategy)
    lines += table("By exit", breakdown(closed, exit_reason))
    lines += table("By symbol", breakdown(closed, lambda t: t["sym"]))
    lines += table("By entry hour (New York)", breakdown(closed, lambda t: t.get("opened", "?")[11:13] + "h"))
    ranked = sorted(closed, key=r_multiple)
    if ranked:
        lines += ["## Best and worst trades", "", "| trade | R | exit | why it was taken |", "| --- | --- | --- | --- |"]
        for t in (ranked[-3:][::-1] + ranked[:3]) if len(ranked) > 6 else ranked[::-1]:
            entry_why = t.get("why", "") if "exit_why" in t else "(not recorded)"
            lines.append("| %s %s | %+.2f | %s | %s |" % (t["sym"], t["opened"], r_multiple(t), exit_reason(t), entry_why))
        lines.append("")
    lines += ["## How to read this", "",
              "- R = profit divided by the money risked (entry to stop). +2R hit the target, -1R hit the stop.",
              "- A strategy needs dozens of trades before its average R means much; until then treat these as a diary.",
              "- To resume a paused strategy, remove it from `paused` in overrides.json."]
    with open(REVIEW, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
