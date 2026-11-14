"""Strategy Generator (the creator). It proposes strategies; it has NO authority to approve them.

Each strategy is a SPEC (hypothesis, rules, parameter grid, markets) plus a causal `signals`
function (see engine.py). Its version is a hash of the spec and the function's source, so any
change is a new version that must be validated again. This module never reads or writes the
validator's criteria or its log (a test enforces that).
"""
import hashlib
import inspect
import json

from .engine import ema, atr, rsi, rolling

HOURLY = ["SPY", "QQQ", "IWM", "XLE", "GLD", "TLT", "BTC-USD", "ETH-USD", "SOL-USD", "CL=F", "EURUSD=X"]


def ema_trend(bars, p):
    c = [b["c"] for b in bars]
    fast, slow, a = ema(c, p["fast"]), ema(c, p["slow"]), atr(bars)
    out = [None] * len(bars)
    for i in range(1, len(bars)):
        side = p["side"]
        crossed = (fast[i] - slow[i]) * side > 0 >= (fast[i - 1] - slow[i - 1]) * side
        if crossed:
            out[i] = (side, c[i] - side * 2 * a[i], c[i] + side * 2 * a[i] * p["rr"])
    return out


def donchian_breakout(bars, p):
    c, a = [b["c"] for b in bars], atr(bars)
    hi = rolling([b["h"] for b in bars], p["n"], max)
    lo = rolling([b["l"] for b in bars], p["n"], min)
    out = [None] * len(bars)
    for i in range(len(bars)):
        if hi[i] is None:
            continue
        if p["side"] > 0 and c[i] > hi[i]:
            out[i] = (1, c[i] - 2 * a[i], c[i] + 2 * a[i] * p["rr"])
        elif p["side"] < 0 and c[i] < lo[i]:
            out[i] = (-1, c[i] + 2 * a[i], c[i] - 2 * a[i] * p["rr"])
    return out


def rsi_reversion(bars, p):
    c, a = [b["c"] for b in bars], atr(bars)
    r, trend = rsi(c, p["rsi_n"]), ema(c, 200)
    out = [None] * len(bars)
    for i in range(200, len(bars)):
        if r[i] < p["low"] and c[i] > trend[i]:          # dip inside an uptrend
            out[i] = (1, c[i] - p["stop_atr"] * a[i], c[i] + a[i])
        elif r[i] > 100 - p["low"] and c[i] < trend[i]:  # pop inside a downtrend
            out[i] = (-1, c[i] + p["stop_atr"] * a[i], c[i] - a[i])
    return out


def breakdown_short(bars, p):
    """The live bot's ETH rule, on hourly bars. 'Daily downtrend' = close under the EMA of
    50 days of hourly closes (50 x 24 bars), so it needs nothing but these bars."""
    c = [b["c"] for b in bars]
    e20, e50, slow = ema(c, 20), ema(c, 50), ema(c, 1200)
    lo = rolling([b["l"] for b in bars], p["n"], min)
    hi8 = rolling([b["h"] for b in bars], 8, max)
    out = [None] * len(bars)
    for i in range(1200, len(bars)):
        if lo[i] is not None and c[i] < lo[i] and c[i] < e20[i] < e50[i] and c[i] < slow[i]:
            stop = max(hi8[i], bars[i]["h"])
            out[i] = (-1, stop, c[i] - p["rr"] * (stop - c[i]))
    return out


STRATEGIES = [
    {"name": "ema_trend", "family": "trend following", "fn": ema_trend, "interval": "1h", "markets": HOURLY,
     "hypothesis": "Once a fast average crosses a slow one, the move tends to continue for longer than "
                   "it reverses (time-series momentum).",
     "grid": [{"fast": f, "slow": s, "rr": rr, "side": sd} for f in (10, 20) for s in (50, 100) for rr in (2, 3)
              for sd in (1, -1)]},
    {"name": "donchian_breakout", "family": "breakout", "fn": donchian_breakout, "interval": "1h", "markets": HOURLY,
     "hypothesis": "A close outside the last n bars' range signals new information that is not yet priced.",
     "grid": [{"n": n, "rr": rr, "side": sd} for n in (24, 48, 96) for rr in (1.5, 2, 3) for sd in (1, -1)]},
    {"name": "rsi_reversion", "family": "mean reversion", "fn": rsi_reversion, "interval": "1h", "markets": HOURLY,
     "hypothesis": "Short-term over-reactions against the long trend partly revert within a day or two.",
     "grid": [{"rsi_n": n, "low": lo, "stop_atr": s} for n in (2, 5, 14) for lo in (10, 20, 30) for s in (1.5, 3)]},
    {"name": "breakdown_short", "family": "breakout (live ETH rule)", "fn": breakdown_short, "interval": "1h",
     "markets": ["ETH-USD", "BTC-USD", "SOL-USD"],
     "hypothesis": "In a daily downtrend, a break of the 12-hour low starts another leg down.",
     "grid": [{"n": n, "rr": rr} for n in (6, 12, 24) for rr in (1, 1.5, 2)]},
]


def version(strategy):
    """Hash of the spec and the signal function's source: change either and it is a new version."""
    spec = {k: v for k, v in strategy.items() if k != "fn"}
    blob = json.dumps(spec, sort_keys=True) + inspect.getsource(strategy["fn"])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
