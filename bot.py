#!/usr/bin/env python3
"""Paper-trading bot. Simulated money only: it never talks to a broker.

It reads free Yahoo prices, opens and closes positions in its own ledger
(state.json), and reinvests: every trade is sized from the current equity.

  python bot.py tick                 # one pass: manage open positions, then look for entries
  python bot.py loop --every 300     # tick every 5 minutes until stopped
  python bot.py report               # write report.md from the ledger
  python bot.py selftest             # check the exit logic offline

Markets:
  crypto  BTC, ETH, SOL (24/7): 15-minute trend breakout, stop under the last 8 bars, target 2R
  stocks  liquid US names: 15-minute opening-range breakout, stop at the range midpoint,
          target 2R, flat by 15:55 New York

Risk: RISK_PCT of equity per trade, at most MAX_OPEN positions, no new entries once the
day is down DAILY_LOSS_PCT, no leverage (notional never exceeds cash).
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
TRADES = os.path.join(HERE, "trades.jsonl")
REPORT = os.path.join(HERE, "report.md")

CFG = {
    "start_cash": 110.0,
    "risk_pct": 0.02,          # 2%: the stock breakout tested at about 0R per trade, so it runs as a small forward test
    "max_open": 3,
    "daily_loss_pct": 0.20,    # no new entries once equity is 20% under the day's start
    "rr": 2.0,
    "min_notional": 5.0,
    # Crypto off: a 2-year hourly backtest (2024-11 to 2026-11, each year tested apart) of trend-filtered
    # breakouts, long and short, found no rule that made money on BTC, ETH and SOL in both years.
    "crypto": [],
    "crypto_short": ["ETH-USD"],   # 1h breakdown in a daily downtrend, see crypto_short_signal
    "stocks": ["SPY", "QQQ", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD",
               "AVGO", "NFLX", "JPM", "XOM", "CVX", "USO"],
    "no_entries_after": "15:30",
    "flat_by": "15:55",
}
cfg_path = os.path.join(HERE, "config.json")
if os.path.exists(cfg_path):
    with open(cfg_path, encoding="utf-8") as fh:
        CFG.update(json.load(fh))


# ---------------------------------------------------------------- time

def ny_offset(utc):
    """Hours from UTC to New York: -4 in summer time, -5 otherwise (US rules since 2007)."""
    y = utc.year
    march = dt.datetime(y, 3, 8 + (6 - dt.date(y, 3, 8).weekday()) % 7, 7)   # 2nd Sunday, 2:00 EST
    nov = dt.datetime(y, 11, 1 + (6 - dt.date(y, 11, 1).weekday()) % 7, 6)   # 1st Sunday, 2:00 EDT
    return -4 if march <= utc.replace(tzinfo=None) < nov else -5


def ny_now(utc=None):
    utc = utc or dt.datetime.now(dt.timezone.utc)
    return utc.replace(tzinfo=None) + dt.timedelta(hours=ny_offset(utc))


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def stock_session_open(ny):
    # shortcut: weekends only, no NYSE holiday calendar; add one before trading near holidays
    return ny.weekday() < 5 and hm("09:30") <= ny.hour * 60 + ny.minute < hm("16:00")


# ---------------------------------------------------------------- data

def bars(sym, interval, rng):
    # query2 first, query1 if Yahoo refuses (cloud runners sometimes get a 429)
    for attempt, host in enumerate(["query2", "query1", "query1"]):
        url = "https://%s.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s" % (host, sym, interval, rng)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 paper-bot"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.load(r)["chart"]["result"][0]
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None not in (o, h, l, c):
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return out


def completed(bs, seconds, now_ts):
    return [b for b in bs if b["t"] + seconds <= now_ts]


def ema(xs, n):
    k, e = 2 / (n + 1), xs[0]
    for x in xs[1:]:
        e = x * k + e * (1 - k)
    return e


def atr(bs, n=14):
    tr = [max(bs[i]["h"] - bs[i]["l"], abs(bs[i]["h"] - bs[i - 1]["c"]), abs(bs[i]["l"] - bs[i - 1]["c"]))
          for i in range(len(bs) - n, len(bs))]
    return sum(tr) / n


# ---------------------------------------------------------------- ledger

def load():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    return {"cash": CFG["start_cash"], "positions": [], "closed": [], "day": {}, "last_signal": {}}


def save(st):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2)


def log(event):
    event["at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    with open(TRADES, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    print("  %s" % json.dumps(event))


def value(pos, price):
    """What the position is worth in cash. A short keeps its notional as collateral (no leverage)."""
    if pos.get("side", 1) > 0:
        return pos["qty"] * price
    return pos["qty"] * (2 * pos["entry"] - price)


def equity(st, prices):
    return st["cash"] + sum(value(p, prices.get(p["sym"], p["entry"])) for p in st["positions"])


# ---------------------------------------------------------------- exits

def walk_exit(pos, bs):
    """First bar after entry that hits the stop or the target. Stop wins a tie (conservative);
    a gap through a level fills at the bar's open."""
    long = pos.get("side", 1) > 0
    for b in bs:
        if b["t"] < pos["entry_bar_t"]:
            continue
        if long and b["l"] <= pos["stop"]:
            return min(b["o"], pos["stop"]), "stop", b["t"]
        if not long and b["h"] >= pos["stop"]:
            return max(b["o"], pos["stop"]), "stop", b["t"]
        if long and b["h"] >= pos["target"]:
            return max(b["o"], pos["target"]), "target", b["t"]
        if not long and b["l"] <= pos["target"]:
            return min(b["o"], pos["target"]), "target", b["t"]
    return None


def close(st, pos, price, why):
    st["cash"] += value(pos, price)
    pnl = pos["qty"] * (price - pos["entry"]) * pos.get("side", 1)
    st["positions"].remove(pos)
    rec = dict(pos, exit=round(price, 6), why=why, pnl=round(pnl, 4))
    st["closed"].append(rec)
    log({"event": "close", "sym": pos["sym"], "exit": round(price, 6), "why": why, "pnl": round(pnl, 4)})


def manage(st, now_ts, ny, prices):
    for pos in list(st["positions"]):
        try:
            bs = bars(pos["sym"], "5m", "5d" if pos["market"] == "crypto" else "1d")
        except Exception as e:
            print("  %s: no data (%s), position kept" % (pos["sym"], e))
            continue
        if not bs:
            continue
        prices[pos["sym"]] = bs[-1]["c"]
        hit = walk_exit(pos, bs)  # the live bar counts too: a stop that traded is a stop
        if hit:
            close(st, pos, hit[0], hit[1])
        elif pos["market"] == "stock" and (ny.hour * 60 + ny.minute >= hm(CFG["flat_by"]) or not stock_session_open(ny)):
            close(st, pos, bs[-1]["c"], "flat by close")


# ---------------------------------------------------------------- entries

def crypto_signal(sym, now_ts):
    # Regime filter: only buy breakouts when the coin is in a daily uptrend. On Sep-Nov 2026 data
    # (TradingView Strategy Tester and a 60-day 15m backtest) every variant of the breakout lost
    # money in the downtrend; this keeps the bot out until the trend turns.
    daily = [b["c"] for b in bars(sym, "1d", "1y")]
    if len(daily) < 60 or daily[-1] <= ema(daily, 50):
        return None
    bs = completed(bars(sym, "15m", "5d"), 900, now_ts)
    if len(bs) < 60:
        return None
    c = [b["c"] for b in bs]
    last = bs[-1]
    e20, e50 = ema(c[-80:], 20), ema(c[-150:], 50)
    prior_high = max(b["h"] for b in bs[-17:-1])           # the 4 hours before the last bar
    if not (last["c"] > prior_high and last["c"] > e20 > e50):
        return None
    a = atr(bs)
    stop = min(b["l"] for b in bs[-8:])
    if last["c"] - stop < 0.5 * a:
        stop = last["c"] - a
    return {"signal_t": last["t"], "ref": last["c"], "stop": stop,
            "why": "15m close %.4f above 4h high %.4f, EMA20 > EMA50" % (last["c"], prior_high)}


def crypto_short_signal(sym, now_ts):
    """1-hour breakdown, only while the coin trades under its daily EMA50. The one crypto rule that
    made money in both years of a 2-year hourly backtest (ETH, 12-bar breakdown, 1.5R target:
    +5% and +17%); its neighbours were mixed, so it runs at 2% risk as a forward test."""
    daily = [b["c"] for b in bars(sym, "1d", "1y")]
    if len(daily) < 60 or daily[-1] >= ema(daily, 50):
        return None
    bs = completed(bars(sym, "1h", "30d"), 3600, now_ts)
    if len(bs) < 200:
        return None
    c = [b["c"] for b in bs]
    last = bs[-1]
    e20, e50 = ema(c[-100:], 20), ema(c[-200:], 50)
    prior_low = min(b["l"] for b in bs[-13:-1])
    if not (last["c"] < prior_low and last["c"] < e20 < e50):
        return None
    return {"signal_t": last["t"], "ref": last["c"], "stop": max(b["h"] for b in bs[-8:]), "side": -1, "rr": 1.5,
            "why": "1h close %.2f under 12h low %.2f, EMA20 < EMA50, daily downtrend" % (last["c"], prior_low)}


def stock_signal(sym, now_ts, ny):
    bs = completed(bars(sym, "5m", "1d"), 300, now_ts)
    day = ny.date()
    def ny_min(b):
        t = dt.datetime.fromtimestamp(b["t"], dt.timezone.utc)
        d = ny_now(t)
        return d.date(), d.hour * 60 + d.minute
    today = [(ny_min(b)[1], b) for b in bs if ny_min(b)[0] == day]
    rng = [b for m, b in today if hm("09:30") <= m < hm("09:45")]
    after = [(m, b) for m, b in today if m >= hm("09:45")]
    if len(rng) < 3 or not after:
        return None
    orh, orl = max(b["h"] for b in rng), min(b["l"] for b in rng)
    trig = next(((m, b) for m, b in after if b["c"] > orh), None)
    if not trig or trig[1]["t"] < after[-1][1]["t"] - 300:   # only a fresh breakout (last 2 bars)
        return None
    return {"signal_t": trig[1]["t"], "ref": trig[1]["c"], "stop": (orh + orl) / 2,
            "why": "5m close %.2f above 15m opening-range high %.2f" % (trig[1]["c"], orh)}


def try_entries(st, now_ts, ny, prices):
    today = ny.date().isoformat()
    eq = equity(st, prices)
    if st["day"].get("date") != today:
        st["day"] = {"date": today, "start_equity": round(eq, 4)}
    if eq < st["day"]["start_equity"] * (1 - CFG["daily_loss_pct"]):
        print("  daily loss limit reached: no new entries today")
        return
    candidates = [("crypto", s) for s in CFG["crypto"]] + [("crypto_short", s) for s in CFG["crypto_short"]]
    minute = ny.hour * 60 + ny.minute
    if stock_session_open(ny) and hm("09:50") <= minute <= hm(CFG["no_entries_after"]):
        # No SPY trend filter: tested on 60 days x 15 symbols it changed nothing (-0.03R vs +0.02R).
        candidates += [("stock", s) for s in CFG["stocks"]]
    held = {p["sym"] for p in st["positions"]}
    for market, sym in candidates:
        if len(st["positions"]) >= CFG["max_open"]:
            break
        if sym in held:
            continue
        try:
            if market == "crypto":
                sig = crypto_signal(sym, now_ts)
            elif market == "crypto_short":
                sig = crypto_short_signal(sym, now_ts)
            else:
                sig = stock_signal(sym, now_ts, ny)
        except Exception as e:
            print("  %s: no data (%s)" % (sym, e))
            continue
        if not sig or st["last_signal"].get(sym) == sig["signal_t"]:
            continue
        side = sig.get("side", 1)
        price = bars(sym, "1m", "1d")[-1]["c"]          # fill at the latest price
        risk_per_unit = (price - sig["stop"]) * side
        if risk_per_unit <= 0:
            continue
        target = price + side * sig.get("rr", CFG["rr"]) * risk_per_unit
        qty = (eq * CFG["risk_pct"]) / risk_per_unit
        qty = min(qty, st["cash"] * 0.98 / price)        # no leverage
        if qty * price < CFG["min_notional"]:
            continue
        st["cash"] -= qty * price
        pos = {"sym": sym, "market": market, "side": side, "qty": round(qty, 8), "entry": price, "stop": round(sig["stop"], 6),
               "target": round(target, 6), "entry_bar_t": int(now_ts) // 300 * 300, "opened": ny.strftime("%Y-%m-%d %H:%M ET"),
               "why": sig["why"]}
        st["positions"].append(pos)
        st["last_signal"][sym] = sig["signal_t"]
        prices[sym] = price
        log({"event": "open", "sym": sym, "market": market, "qty": pos["qty"], "entry": round(price, 6),
             "stop": pos["stop"], "target": pos["target"], "risk_usd": round(qty * risk_per_unit, 2), "why": sig["why"]})


# ---------------------------------------------------------------- commands

def tick():
    now_ts = time.time()
    ny = ny_now()
    st = load()
    prices = {}
    print("tick %s" % ny.strftime("%Y-%m-%d %H:%M ET"))
    manage(st, now_ts, ny, prices)
    try_entries(st, now_ts, ny, prices)
    st["equity"] = round(equity(st, prices), 4)
    st["updated"] = ny.strftime("%Y-%m-%d %H:%M ET")
    save(st)
    report(st)
    print("equity %.2f USD (start %.2f), cash %.2f, open %d" % (st["equity"], CFG["start_cash"], st["cash"], len(st["positions"])))


def report(st=None):
    st = st or load()
    eq = st.get("equity", st["cash"])
    wins = [t for t in st["closed"] if t["pnl"] > 0]
    lines = ["# Paper bot report", "",
             "Simulated money on Yahoo prices; no broker, no real orders. Updated %s." % st.get("updated", "-"), "",
             "| | |", "| --- | --- |",
             "| Start | %.2f USD |" % CFG["start_cash"],
             "| Equity | %.2f USD (%+.1f%%) |" % (eq, 100 * (eq / CFG["start_cash"] - 1)),
             "| Cash | %.2f USD |" % st["cash"],
             "| Closed trades | %d (%d won) |" % (len(st["closed"]), len(wins)), "",
             "## Open", "", "| symbol | market | qty | entry | stop | target | opened | why |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    lines += ["| %s | %s | %.6g | %.4f | %.4f | %.4f | %s | %s |" % (p["sym"], p["market"], p["qty"], p["entry"], p["stop"],
              p["target"], p["opened"], p["why"]) for p in st["positions"]]
    lines += ["", "## Closed", "", "| symbol | entry | exit | why | P&L USD | opened |",
              "| --- | --- | --- | --- | --- | --- |"]
    lines += ["| %s | %.4f | %.4f | %s | %+.2f | %s |" % (t["sym"], t["entry"], t["exit"], t["why"], t["pnl"], t["opened"])
              for t in st["closed"]]
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def selftest():
    pos = {"entry": 100, "stop": 99, "target": 102, "entry_bar_t": 10}
    b = lambda t, o, h, l, c: {"t": t, "o": o, "h": h, "l": l, "c": c}
    assert walk_exit(pos, [b(5, 100, 105, 90, 100)]) is None, "bars before entry are ignored"
    assert walk_exit(pos, [b(10, 100, 101, 99.5, 100.5)]) is None
    assert walk_exit(pos, [b(10, 100, 102.5, 99.5, 102)])[:2] == (102, "target")
    assert walk_exit(pos, [b(10, 100, 103, 98, 101)])[:2] == (99, "stop"), "stop wins a tie"
    assert walk_exit(pos, [b(10, 97, 98, 96, 97)])[:2] == (97, "stop"), "gap down fills at the open"
    assert walk_exit(pos, [b(10, 103, 104, 102.5, 103)])[:2] == (103, "target"), "gap up fills at the open"
    short = {"sym": "X", "side": -1, "qty": 2, "entry": 100, "stop": 101, "target": 98.5, "entry_bar_t": 10}
    assert walk_exit(short, [b(10, 100, 101.5, 99, 100)])[:2] == (101, "stop"), "short stop on a high"
    assert walk_exit(short, [b(10, 100, 100.5, 98, 99)])[:2] == (98.5, "target"), "short target on a low"
    assert walk_exit(short, [b(10, 102, 103, 101.5, 102)])[:2] == (102, "stop"), "short gap up fills at the open"
    st = {"cash": 0.0, "positions": [dict(short)], "closed": []}
    assert value(short, 98.5) == 203 and equity(st, {}) == 200   # collateral 200 + 3 profit; unpriced = flat
    global log
    log, saved = (lambda e: None), log
    close(st, st["positions"][0], 98.5, "target")
    log = saved
    assert st["cash"] == 203 and st["closed"][0]["pnl"] == 3
    assert ny_offset(dt.datetime(2026, 11, 13, 15, tzinfo=dt.timezone.utc)) == -5
    assert ny_offset(dt.datetime(2026, 7, 1, 15, tzinfo=dt.timezone.utc)) == -4
    assert ny_offset(dt.datetime(2026, 3, 8, 7, tzinfo=dt.timezone.utc)) == -4    # DST starts 2026-03-08
    assert ny_offset(dt.datetime(2026, 11, 1, 6, tzinfo=dt.timezone.utc)) == -5   # DST ends 2026-11-01
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["tick", "loop", "report", "selftest"])
    ap.add_argument("--every", type=int, default=300, help="seconds between ticks in loop mode")
    a = ap.parse_args()
    if a.cmd == "tick":
        tick()
    elif a.cmd == "loop":
        while True:
            try:
                tick()
            except Exception as e:  # a bad tick must not kill the loop
                print("tick failed: %s" % e)
            time.sleep(a.every)
    elif a.cmd == "report":
        report()
    else:
        selftest()


if __name__ == "__main__":
    main()
