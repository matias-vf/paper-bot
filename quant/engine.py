"""One simulation engine for research and validation.

A strategy is a function `signals(bars, params)` that returns, for every bar i, either None or
(side, stop, target) decided at the CLOSE of bar i using bars[0..i] only. The engine enters at the
OPEN of bar i+1, exits on the first bar that touches the stop (checked first: conservative) or the
target, or after `max_hold` bars at that bar's close. One position at a time.

Costs are a round-trip fraction of price per market: commission + half spread x2 + slippage.
Yahoo has no spread or order book, so these are ESTIMATES (see COSTS); the validator also runs
every strategy at twice these costs.
"""

# round-trip cost as a fraction of price (commission + spread + slippage), estimated
COSTS = {"stock": 0.0006, "index": 0.0004, "etf": 0.0006, "crypto": 0.0020, "commodity": 0.0006, "fx": 0.0002}


def ema(xs, n):
    k, out = 2 / (n + 1), []
    for x in xs:
        out.append(x if not out else x * k + out[-1] * (1 - k))
    return out


def atr(bars, n=14):
    out, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"] if prev is None else max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev))
        out.append(tr if not out else (out[-1] * (n - 1) + tr) / n)   # Wilder smoothing: causal
        prev = b["c"]
    return out


def rsi(closes, n=14):
    out, up, dn = [50.0], 0.0, 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        up = (up * (n - 1) + max(d, 0)) / n
        dn = (dn * (n - 1) + max(-d, 0)) / n
        out.append(100.0 if dn == 0 else 100 - 100 / (1 + up / dn))
    return out


def rolling(xs, n, fn):
    """fn over the n values BEFORE i (excludes i itself), None while warming up."""
    return [None if i < n else fn(xs[i - n:i]) for i in range(len(xs))]


def run(bars, sigs, cost=0.0, max_hold=48, start=0, end=None):
    """Trades whose signal bar is in [start, end). Returns dicts with entry/exit index, side, prices,
    ret (fraction, after cost) and r (multiple of the initial stop distance, after cost)."""
    end = len(bars) - 1 if end is None else min(end, len(bars) - 1)
    trades, i = [], start
    while i < end:
        s = sigs[i]
        if not s:
            i += 1
            continue
        side, stop, target = s
        j = i + 1
        entry = bars[j]["o"]
        if (entry - stop) * side <= 0 or (target - entry) * side <= 0:   # gapped through a level: skip
            i += 1
            continue
        exit_px, k = None, j
        while k < len(bars) and k <= j + max_hold:
            b = bars[k]
            if side > 0 and b["l"] <= stop:
                exit_px = min(b["o"], stop) if k > j else stop
            elif side < 0 and b["h"] >= stop:
                exit_px = max(b["o"], stop) if k > j else stop
            elif side > 0 and b["h"] >= target:
                exit_px = max(b["o"], target) if k > j else target
            elif side < 0 and b["l"] <= target:
                exit_px = min(b["o"], target) if k > j else target
            if exit_px is not None:
                break
            k += 1
        if exit_px is None:
            k = min(k, len(bars) - 1)
            exit_px = bars[k]["c"]
        ret = side * (exit_px / entry - 1) - cost
        risk = abs(entry - stop) / entry
        trades.append({"i_in": j, "i_out": k, "side": side, "entry": entry, "exit": exit_px, "stop": stop,
                       "target": target, "ret": ret, "r": ret / risk})
        i = k   # next signal only after this trade closed
    return trades


def summary(trades):
    if not trades:
        return {"n": 0, "mean_r": 0.0, "sum_r": 0.0, "win": 0.0, "ret": 0.0}
    eq = 1.0
    for t in trades:
        eq *= 1 + t["ret"]
    rs = [t["r"] for t in trades]
    return {"n": len(rs), "mean_r": sum(rs) / len(rs), "sum_r": sum(rs), "win": sum(r > 0 for r in rs) / len(rs),
            "ret": eq - 1}
