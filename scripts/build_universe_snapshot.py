#!/usr/bin/env python3
"""Build a dated universe snapshot for `configs/universe/` (issue #3).

**This is a manual data tool, not part of the runtime package.** ADR
2026-07-22 §2 forbids fetching index membership at run time — it would make the
same ``as_of`` replay differently on two days. A human runs this, inspects the
diff, and commits the result; nothing in `src/smm/` imports it.

Usage::

    pip install -e ".[market]" lxml     # pandas + lxml, both manual-only
    python scripts/build_universe_snapshot.py --as-of 2026-07-22

Sources (both read once, by hand):

- S&P 500: Wikipedia "List of S&P 500 companies" — carries **GICS** sectors
- Nasdaq-100: Wikipedia "List of NASDAQ-100 companies" — carries **ICB**
  industries, which are mapped to GICS below

Where a symbol is in both indices the **GICS** sector wins, since it is the
classification the constitution's sector benchmarks are keyed on. A symbol whose
sector cannot be resolved is written with an **empty** sector rather than a
guess: per the M2 ADR a missing sector propagates and drops the symbol from
candidates, which is the safe outcome. A wrong sector would silently corrupt the
relative-strength ranking instead.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
from datetime import date
from pathlib import Path

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
_HEADERS = {"User-Agent": "Mozilla/5.0 (swingMomentum; one-time universe snapshot)"}

#: GICS sector name -> snake_case key used in config `sector_benchmarks`.
GICS_TO_KEY = {
    "Information Technology": "information_technology",
    "Financials": "financials",
    "Health Care": "health_care",
    "Consumer Discretionary": "consumer_discretionary",
    "Consumer Staples": "consumer_staples",
    "Energy": "energy",
    "Industrials": "industrials",
    "Materials": "materials",
    "Utilities": "utilities",
    "Real Estate": "real_estate",
    "Communication Services": "communication_services",
}

# There is deliberately no ICB -> GICS mapping here.
#
# The first version of this script mapped the Nasdaq-100 page's ICB industries
# onto GICS keys. Cross-checking the 15 Nasdaq-only names against an
# independent classification found **4 wrong** (NBIS, PDD, SPCX, TRI) — a 27%
# error rate. ICB and GICS genuinely disagree about where some businesses sit;
# SpaceX is ICB "Telecommunications" (Starlink) but GICS Industrials /
# Aerospace & Defense.
#
# A systematic-looking table made that a guess in disguise. Per the project's
# own rule, a missing sector propagates and drops the symbol from candidates,
# while a wrong sector silently corrupts the relative-strength ranking for
# every peer in that sector. So Nasdaq-only names get an EMPTY sector until a
# real GICS source covers them.


def to_yahoo_symbol(symbol: str) -> str:
    """Wikipedia writes share classes as ``BRK.B``; Yahoo expects ``BRK-B``."""
    return symbol.strip().upper().replace(".", "-")


def _read_tables(url: str):
    import pandas as pd  # noqa: PLC0415 - manual tool, not a package dependency

    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        html = response.read().decode()
    return pd.read_html(io.StringIO(html))


def fetch_sp500() -> dict[str, tuple[str, str]]:
    """``{symbol: (name, sector_key)}`` for the S&P 500."""
    frame = _read_tables(SP500_URL)[0]
    out: dict[str, tuple[str, str]] = {}
    for _, row in frame.iterrows():
        symbol = to_yahoo_symbol(str(row["Symbol"]))
        sector = GICS_TO_KEY.get(str(row["GICS Sector"]).strip(), "")
        out[symbol] = (str(row["Security"]).strip(), sector)
    return out


def fetch_ndx() -> dict[str, tuple[str, str]]:
    """``{symbol: (name, sector_key)}`` for the Nasdaq-100.

    Sector is always empty: the page carries ICB industries, which are not
    safely convertible to GICS (see the note above ``GICS_TO_KEY``). Names that
    are also in the S&P 500 pick up a real GICS sector there.
    """
    frame = _read_tables(NDX_URL)[0]
    out: dict[str, tuple[str, str]] = {}
    for _, row in frame.iterrows():
        symbol = to_yahoo_symbol(str(row["Ticker"]))
        out[symbol] = (str(row["Company"]).strip(), "")
    return out


def verify_symbols(symbols: list[str]) -> list[str]:
    """Return symbols with no tradeable price data.

    A row count in the expected range cannot catch one bad ticker hidden among
    a hundred good ones. Asking the market data provider whether each symbol
    actually trades can: a fabricated, delisted, or mis-formatted ticker
    (``BRK.B`` instead of ``BRK-B``) returns nothing.
    """
    import yfinance  # noqa: PLC0415 - manual tool, not a package dependency

    missing: list[str] = []
    for start in range(0, len(symbols), 120):
        chunk = symbols[start : start + 120]
        frame = yfinance.download(
            chunk, period="1mo", auto_adjust=False, progress=False, threads=True
        )
        if frame is None or frame.empty:
            sys.exit("verification aborted: provider returned nothing (rate limited?)")
        close = frame["Close"]
        missing.extend(
            s for s in chunk if s not in close.columns or int(close[s].notna().sum()) == 0
        )
    return missing


def build_rows(as_of: date) -> list[dict[str, str]]:
    sp500 = fetch_sp500()
    ndx = fetch_ndx()
    if not (400 < len(sp500) < 600):
        sys.exit(f"refusing to write: S&P 500 returned {len(sp500)} rows")
    if not (80 < len(ndx) < 120):
        sys.exit(f"refusing to write: Nasdaq-100 returned {len(ndx)} rows")

    rows: list[dict[str, str]] = []
    for symbol in sorted(set(sp500) | set(ndx)):
        in_sp, in_ndx = symbol in sp500, symbol in ndx
        membership = "both" if in_sp and in_ndx else ("sp500" if in_sp else "ndx100")
        # GICS wins for dual members: it is what sector_benchmarks is keyed on.
        name, sector = sp500[symbol] if in_sp else ndx[symbol]
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "index_membership": membership,
                "sector": sector,
                "snapshot_date": as_of.isoformat(),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="Snapshot date (YYYY-MM-DD)")
    parser.add_argument(
        "--out-dir",
        default=Path(__file__).resolve().parents[1] / "configs" / "universe",
        type=Path,
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Check every symbol actually trades before writing (recommended)",
    )
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of)

    rows = build_rows(as_of)

    if args.verify:
        missing = verify_symbols([r["symbol"] for r in rows])
        if missing:
            sys.exit(f"refusing to write: {len(missing)} symbols have no price data: {missing}")
        print(f"verified: all {len(rows)} symbols return price data")

    target = args.out_dir / f"{as_of.isoformat()}_sp500_ndx.csv"
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["symbol", "name", "index_membership", "sector", "snapshot_date"]
        )
        writer.writeheader()
        writer.writerows(rows)

    missing = [r["symbol"] for r in rows if not r["sector"]]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["index_membership"]] = counts.get(row["index_membership"], 0) + 1
    print(f"wrote {target} — {len(rows)} symbols {counts}")
    if missing:
        print(f"  {len(missing)} without a sector (will be excluded as missing): {missing}")


if __name__ == "__main__":
    main()
