"""SQLite research store.

Prices are a cache (rebuildable from Yahoo), so data/quant.db is not committed. The trading
ledger stays in git as trades.jsonl / state.json (small, diffable); `import_ledger` copies it
into the database so research can join trades with prices.
"""
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(HERE, "data", "quant.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT    NOT NULL,
    interval TEXT    NOT NULL,          -- '5m', '1h', '1d'
    ts       INTEGER NOT NULL,          -- bar start, unix seconds UTC
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume   REAL,
    PRIMARY KEY (symbol, interval, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS instruments (
    symbol     TEXT PRIMARY KEY,
    market     TEXT NOT NULL,           -- stock, etf, crypto, commodity, fx, index
    note       TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,          -- 'bot', 'tradingview', 'backtest:<name>'
    strategy    TEXT,
    symbol      TEXT NOT NULL,
    side        INTEGER NOT NULL,       -- +1 long, -1 short
    qty         REAL NOT NULL,
    entry       REAL NOT NULL,
    entry_at    TEXT NOT NULL,          -- ISO UTC
    stop        REAL,
    target      REAL,
    exit        REAL,
    exit_at     TEXT,
    exit_reason TEXT,
    pnl         REAL,
    why         TEXT,
    UNIQUE (source, symbol, entry_at)
);

CREATE TABLE IF NOT EXISTS quality (
    symbol   TEXT NOT NULL,
    interval TEXT NOT NULL,
    checked  TEXT NOT NULL,
    bars     INTEGER, first_ts INTEGER, last_ts INTEGER,
    bad_bars INTEGER, gaps INTEGER, max_gap_s INTEGER,
    PRIMARY KEY (symbol, interval)
);
"""


def connect(path=DEFAULT_DB):
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def upsert_bars(con, symbol, interval, rows):
    """rows: dicts with t, o, h, l, c and optional v. Re-downloads overwrite (Yahoo revises the last bar)."""
    con.executemany(
        "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
        [(symbol, interval, int(r["t"]), r["o"], r["h"], r["l"], r["c"], r.get("v")) for r in rows])
    con.commit()
    return len(rows)


def load_bars(con, symbol, interval, since=None):
    q = "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol=? AND interval=?"
    args = [symbol, interval]
    if since is not None:
        q += " AND ts >= ?"
        args.append(int(since))
    return [dict(t=t, o=o, h=h, l=l, c=c, v=v) for t, o, h, l, c, v in con.execute(q + " ORDER BY ts", args)]


def last_ts(con, symbol, interval):
    row = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()
    return row[0]


def import_ledger(con, trades_path, source="bot"):
    """Pair the bot's open/close events (trades.jsonl) into round trips. Safe to run again."""
    if not os.path.exists(trades_path):
        return 0
    open_by_sym, n = {}, 0
    with open(trades_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev["event"] == "open":
                open_by_sym[ev["sym"]] = ev
            elif ev["event"] == "close" and ev["sym"] in open_by_sym:
                o = open_by_sym.pop(ev["sym"])
                cur = con.execute(
                    "INSERT OR IGNORE INTO trades (source, strategy, symbol, side, qty, entry, entry_at, stop, target,"
                    " exit, exit_at, exit_reason, pnl, why) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (source, o.get("market"), o["sym"], o.get("side", 1), o["qty"], o["entry"], o["at"], o.get("stop"),
                     o.get("target"), ev["exit"], ev["at"], ev["why"], ev["pnl"], o.get("why")))
                n += cur.rowcount
    con.commit()
    return n
