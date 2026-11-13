"""Market data: download Yahoo bars into the SQLite store and check their quality.

  python -m quant.data update                 # every instrument, 1d + 1h + 5m, incremental
  python -m quant.data update --interval 1h --symbols ETH-USD,SPY
  python -m quant.data quality                # print the quality table
  python -m quant.data import-ledger          # copy the bot's trades.jsonl into the database
"""
import argparse
import json
import os
import time
import urllib.request

from . import db

UNIVERSE = {
    "index":     ["SPY", "QQQ", "IWM", "DIA"],
    "stock":     ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "NFLX", "JPM", "XOM", "CVX"],
    "etf":       ["TLT", "GLD", "USO", "XLE", "XLF", "XLK", "XLU", "XLP"],
    "crypto":    ["BTC-USD", "ETH-USD", "SOL-USD"],
    "commodity": ["CL=F", "GC=F", "NG=F", "HG=F"],
    "fx":        ["EURUSD=X", "USDJPY=X", "GBPUSD=X"],
}
# Yahoo's history limits per interval
RANGES = {"1d": "10y", "1h": "730d", "5m": "60d"}
SECONDS = {"1d": 86400, "1h": 3600, "5m": 300}


def fetch(symbol, interval, rng):
    last = None
    for attempt, host in enumerate(["query2", "query1", "query1"]):
        url = "https://%s.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s" % (host, symbol, interval, rng)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 paper-bot-research"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.load(r)["chart"]["result"][0]
            break
        except Exception as e:  # 429s and 5xx: try the other host, then once more
            last = e
            time.sleep(3 * (attempt + 1))
    else:
        raise last
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": (q.get("volume") or [None] * (i + 1))[i]})
    return out


def check(rows, interval):
    """Data-quality counts. A 'bad' bar has a non-positive price or high < max(open, close) or
    low > min(open, close). A 'gap' is a jump bigger than 3 bar lengths that is not a weekend or
    an overnight session break (those are normal for stocks and futures)."""
    bad = sum(1 for r in rows if min(r["o"], r["h"], r["l"], r["c"]) <= 0
              or r["h"] < max(r["o"], r["c"]) - 1e-9 or r["l"] > min(r["o"], r["c"]) + 1e-9)
    step = SECONDS[interval]
    gaps, max_gap = 0, 0
    for a, b in zip(rows, rows[1:]):
        d = b["t"] - a["t"]
        max_gap = max(max_gap, d)
        if interval != "1d" and d > 3 * step and d < 12 * 3600:   # intraday hole inside a session
            gaps += 1
        if interval == "1d" and d > 5 * 86400:                     # more than a long weekend missing
            gaps += 1
    return {"bars": len(rows), "bad_bars": bad, "gaps": gaps, "max_gap_s": max_gap,
            "first_ts": rows[0]["t"] if rows else None, "last_ts": rows[-1]["t"] if rows else None}


def update(con, symbols, intervals, pause=0.4, log=print):
    for interval in intervals:
        for sym in symbols:
            have = db.last_ts(con, sym, interval)
            # incremental: a short range is enough once the history is stored
            rng = RANGES[interval] if have is None else {"1d": "1mo", "1h": "5d", "5m": "5d"}[interval]
            try:
                rows = fetch(sym, interval, rng)
            except Exception as e:
                log("  %-9s %-3s FAILED: %s" % (sym, interval, e))
                continue
            db.upsert_bars(con, sym, interval, rows)
            stored = db.load_bars(con, sym, interval)
            q = check(stored, interval)
            con.execute("INSERT OR REPLACE INTO quality VALUES (?,?,datetime('now'),?,?,?,?,?,?)",
                        (sym, interval, q["bars"], q["first_ts"], q["last_ts"], q["bad_bars"], q["gaps"], q["max_gap_s"]))
            con.commit()
            log("  %-9s %-3s +%-5d stored %-6d bad %d gaps %d" % (sym, interval, len(rows), q["bars"], q["bad_bars"], q["gaps"]))
            time.sleep(pause)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["update", "quality", "import-ledger"])
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--interval", help="1d, 1h or 5m (default all three)")
    ap.add_argument("--symbols", help="comma-separated (default the whole universe)")
    a = ap.parse_args()
    con = db.connect(a.db)
    if a.cmd == "update":
        for market, syms in UNIVERSE.items():
            con.executemany("INSERT OR IGNORE INTO instruments VALUES (?,?,NULL)", [(s, market) for s in syms])
        symbols = a.symbols.split(",") if a.symbols else [s for v in UNIVERSE.values() for s in v]
        update(con, symbols, [a.interval] if a.interval else ["1d", "1h", "5m"])
    elif a.cmd == "quality":
        for row in con.execute("SELECT symbol, interval, bars, bad_bars, gaps, date(first_ts,'unixepoch'), "
                               "datetime(last_ts,'unixepoch') FROM quality ORDER BY interval, symbol"):
            print("%-9s %-3s %6d bars  bad %d  gaps %3d  %s .. %s" % row)
    else:
        n = db.import_ledger(con, os.path.join(db.HERE, "trades.jsonl"))
        total = con.execute("SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades").fetchone()
        print("imported %d new round trips; %d in the database, total P&L %.2f USD" % (n, total[0], total[1]))


if __name__ == "__main__":
    main()
