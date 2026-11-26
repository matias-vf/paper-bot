# Bot review

Generated 2026-11-26 01:35 UTC from 5 closed trades. Equity 117.88 USD (start 110.00).

**Too few trades to judge anything yet** (5 of the 8 needed). The numbers below are a log, not evidence.

## Actions taken

- none

Paused strategies: none. Risk scale: normal.

## What went well and what went badly

### By strategy

| | trades | won | total R | avg R | P&L USD |
| --- | --- | --- | --- | --- | --- |
| crypto_short | 2 | 2 | +3.00 | +1.50 | +6.73 |
| rsi_reversion | 2 | 2 | +1.43 | +0.71 | +0.70 |
| stock | 1 | 1 | +0.48 | +0.48 | +0.45 |

### By exit

| | trades | won | total R | avg R | P&L USD |
| --- | --- | --- | --- | --- | --- |
| flat by close | 1 | 1 | +0.48 | +0.48 | +0.45 |
| target | 4 | 4 | +4.43 | +1.11 | +7.43 |

### By symbol

| | trades | won | total R | avg R | P&L USD |
| --- | --- | --- | --- | --- | --- |
| CL=F | 1 | 1 | +0.92 | +0.92 | +0.52 |
| ETH-USD | 2 | 2 | +3.00 | +1.50 | +6.73 |
| EURUSD=X | 1 | 1 | +0.51 | +0.51 | +0.18 |
| MSFT | 1 | 1 | +0.48 | +0.48 | +0.45 |

### By entry hour (New York)

| | trades | won | total R | avg R | P&L USD |
| --- | --- | --- | --- | --- | --- |
| 09h | 2 | 2 | +1.41 | +0.70 | +0.98 |
| 12h | 1 | 1 | +0.51 | +0.51 | +0.18 |
| 16h | 1 | 1 | +1.50 | +1.50 | +3.41 |
| 22h | 1 | 1 | +1.50 | +1.50 | +3.31 |

## Best and worst trades

| trade | R | exit | why it was taken |
| --- | --- | --- | --- |
| ETH-USD 2026-11-13 22:52 ET | +1.50 | target | 1h close 1910.53 under 12h low 1912.44, EMA20 < EMA50, daily downtrend |
| ETH-USD 2026-11-14 16:39 ET | +1.50 | target | 1h close 1782.77 under 12h low 1810.80, EMA20 < EMA50, daily downtrend |
| CL=F 2026-11-17 09:55 ET | +0.92 | target | rsi_reversion {"low": 10, "rsi_n": 5, "stop_atr": 1.5} (PROBATION, 4e672ec6161b23e5) on the 1h close 87.0000 |
| EURUSD=X 2026-11-25 12:01 ET | +0.51 | target | rsi_reversion {"low": 30, "rsi_n": 14, "stop_atr": 3} (PROBATION, 4e672ec6161b23e5) on the 1h close 1.0982 |
| MSFT 2026-11-13 09:56 ET | +0.48 | flat by close | (not recorded) |

## How to read this

- R = profit divided by the money risked (entry to stop). +2R hit the target, -1R hit the stop.
- A strategy needs dozens of trades before its average R means much; until then treat these as a diary.
- To resume a paused strategy, remove it from `paused` in overrides.json.
