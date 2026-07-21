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

#: ICB industry -> GICS key, for Nasdaq-100 names absent from the S&P 500.
#: Only unambiguous top-level equivalences are listed; anything else resolves to
#: an empty sector on purpose.
ICB_TO_KEY = {
    "Technology": "information_technology",
    "Health Care": "health_care",
    "Consumer Discretionary": "consumer_discretionary",
    "Consumer Staples": "consumer_staples",
    "Consumer Services": "consumer_discretionary",
    "Financials": "financials",
    "Industrials": "industrials",
    "Energy": "energy",
    "Basic Materials": "materials",
    "Utilities": "utilities",
    "Real Estate": "real_estate",
    "Telecommunications": "communication_services",
}


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
    """``{symbol: (name, sector_key)}`` for the Nasdaq-100."""
    frame = _read_tables(NDX_URL)[0]
    industry_col = next(c for c in frame.columns if "ICB Industry" in str(c))
    out: dict[str, tuple[str, str]] = {}
    for _, row in frame.iterrows():
        symbol = to_yahoo_symbol(str(row["Ticker"]))
        sector = ICB_TO_KEY.get(str(row[industry_col]).strip(), "")
        out[symbol] = (str(row["Company"]).strip(), sector)
    return out


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
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of)

    rows = build_rows(as_of)
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
