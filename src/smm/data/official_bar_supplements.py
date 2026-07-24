"""Reviewed, offline official-exchange bars for exact isolated provider holes.

This is not a second live provider. Runtime never fetches the URLs recorded in
the snapshot. A committed point-in-time CSV may fill one exact session only
after its source, security identity and provider adjustment basis all validate.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError as PydanticValidationError

from smm.config.schema import OfficialBarSupplementSection
from smm.core.errors import DataValidationError
from smm.domain.models import Bar

_SNAPSHOT = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})_official_bar_supplements\.csv$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]*$")
_CIK = re.compile(r"^\d{10}$")
_FIELDS = (
    "supplement_id",
    "symbol",
    "session",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "adj_factor",
    "bar_source_url",
    "bar_source_title",
    "registrant_cik",
    "identity_effective_date",
    "identity_source_url",
    "identity_source_title",
    "adjustment_method",
    "adjustment_previous_session",
    "adjustment_previous_close",
    "adjustment_previous_adj_close",
    "adjustment_next_session",
    "adjustment_next_close",
    "adjustment_next_adj_close",
)


def official_bar_supplement_snapshot_is_visible(
    directory: Path | str,
    *,
    as_of: date,
) -> bool:
    """Whether any committed supplement snapshot is visible without look-ahead."""
    for path in Path(directory).glob("*_official_bar_supplements.csv"):
        match = _SNAPSHOT.fullmatch(path.name)
        if match is None:
            continue
        try:
            snapshot_date = date.fromisoformat(match.group("day"))
        except ValueError:
            continue
        if snapshot_date <= as_of:
            return True
    return False


@dataclass(frozen=True, slots=True)
class OfficialBarSupplement:
    supplement_id: str
    bar: Bar
    bar_source_url: str
    bar_source_title: str
    registrant_cik: str
    identity_effective_date: date
    identity_source_url: str
    identity_source_title: str
    adjustment_method: str
    adjustment_previous_session: date
    adjustment_previous_close: float
    adjustment_previous_adj_close: float
    adjustment_next_session: date
    adjustment_next_close: float
    adjustment_next_adj_close: float


@dataclass(frozen=True, slots=True)
class OfficialBarSupplementSnapshot:
    snapshot_id: str
    snapshot_date: date
    sha256: str
    supplements: tuple[OfficialBarSupplement, ...]


@dataclass(frozen=True, slots=True)
class OfficialBarSupplementVerification:
    verification_kind: str
    event_id: str
    symbol: str
    session: date
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    raw_volume: float
    adj_close: float
    adj_factor: float
    bar_source_url: str
    registrant_cik: str
    identity_effective_date: date
    identity_source_url: str
    adjustment_method: str
    adjustment_previous_session: date
    adjustment_previous_close: float
    adjustment_previous_adj_close: float
    adjustment_next_session: date
    adjustment_next_close: float
    adjustment_next_adj_close: float
    snapshot_id: str
    snapshot_sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "adj_close": f"{self.adj_close:.6f}",
            "adj_factor": f"{self.adj_factor:.12f}",
            "adjustment_method": self.adjustment_method,
            "adjustment_next_adj_close": f"{self.adjustment_next_adj_close:.6f}",
            "adjustment_next_close": f"{self.adjustment_next_close:.6f}",
            "adjustment_next_session": self.adjustment_next_session.isoformat(),
            "adjustment_previous_adj_close": (
                f"{self.adjustment_previous_adj_close:.6f}"
            ),
            "adjustment_previous_close": f"{self.adjustment_previous_close:.6f}",
            "adjustment_previous_session": self.adjustment_previous_session.isoformat(),
            "bar_source_url": self.bar_source_url,
            "event_id": self.event_id,
            "identity_effective_date": self.identity_effective_date.isoformat(),
            "identity_source_url": self.identity_source_url,
            "raw_close": f"{self.raw_close:.6f}",
            "raw_high": f"{self.raw_high:.6f}",
            "raw_low": f"{self.raw_low:.6f}",
            "raw_open": f"{self.raw_open:.6f}",
            "raw_volume": f"{self.raw_volume:.6f}",
            "registrant_cik": self.registrant_cik,
            "session": self.session.isoformat(),
            "snapshot_id": self.snapshot_id,
            "snapshot_sha256": self.snapshot_sha256,
            "symbol": self.symbol,
            "verification_kind": self.verification_kind,
        }


def load_official_bar_supplement_snapshot(
    directory: Path | str,
    *,
    as_of: date,
    cfg: OfficialBarSupplementSection,
) -> OfficialBarSupplementSnapshot:
    """Load the newest committed supplement snapshot visible at ``as_of``."""
    candidates: list[tuple[date, Path]] = []
    for path in Path(directory).glob("*_official_bar_supplements.csv"):
        match = _SNAPSHOT.fullmatch(path.name)
        if match is None:
            continue
        try:
            snapshot_date = date.fromisoformat(match.group("day"))
        except ValueError:
            continue
        if snapshot_date <= as_of:
            candidates.append((snapshot_date, path))
    if not candidates:
        raise DataValidationError(
            f"no official-bar supplement snapshot is visible at {as_of}"
        )
    snapshot_date, path = max(candidates, key=lambda item: (item[0], item[1].name))
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataValidationError(
            f"{path.name}: official-bar supplement snapshot is not UTF-8"
        ) from exc
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != _FIELDS:
        raise DataValidationError(
            f"{path.name}: official-bar columns must be exactly {', '.join(_FIELDS)}"
        )
    supplements = tuple(
        _parse_supplement(row, path=path, snapshot_date=snapshot_date, cfg=cfg)
        for row in reader
    )
    if not supplements:
        raise DataValidationError(f"{path.name}: official-bar snapshot is empty")
    _validate_uniqueness(supplements, path)
    return OfficialBarSupplementSnapshot(
        snapshot_id=path.stem,
        snapshot_date=snapshot_date,
        sha256=hashlib.sha256(payload).hexdigest(),
        supplements=tuple(
            sorted(
                supplements,
                key=lambda item: (item.bar.symbol, item.bar.date, item.supplement_id),
            )
        ),
    )


def reconcile_official_bar_supplements(
    bars: list[Bar] | tuple[Bar, ...],
    *,
    calendar: list[date] | tuple[date, ...] | None,
    snapshot: OfficialBarSupplementSnapshot,
    cfg: OfficialBarSupplementSection,
) -> tuple[list[Bar], tuple[OfficialBarSupplementVerification, ...]]:
    """Repair or replay exact reviewed sessions; reject every source conflict."""
    ordered = sorted(bars, key=lambda item: item.date)
    if calendar is None or not ordered:
        return ordered, ()
    symbol = ordered[0].symbol
    if {bar.symbol for bar in ordered} != {symbol}:
        raise DataValidationError("official-bar reconciliation requires one symbol")
    sessions = sorted(set(calendar))
    if not sessions:
        return ordered, ()
    first, last = ordered[0].date, ordered[-1].date
    expected = {session for session in sessions if first <= session <= last}
    present = {bar.date: bar for bar in ordered}
    missing = sorted(expected - set(present))
    if len(missing) > cfg.max_missing_sessions_per_symbol:
        raise DataValidationError(
            f"{symbol}: {len(missing)} provider sessions are missing; official-bar "
            f"policy permits at most {cfg.max_missing_sessions_per_symbol}"
        )

    candidates = [
        item
        for item in snapshot.supplements
        if item.bar.symbol == symbol and first <= item.bar.date <= last
    ]
    records: list[OfficialBarSupplementVerification] = []
    for supplement in candidates:
        session = supplement.bar.date
        if session not in expected:
            raise DataValidationError(
                f"{symbol}: official supplement {session} is outside the trading calendar"
            )
        provider_bar = present.get(session)
        if provider_bar is None:
            if session not in missing:
                raise DataValidationError(
                    f"{symbol}: official supplement {session} is not an exact provider hole"
                )
            present[session] = supplement.bar
        elif provider_bar != supplement.bar:
            raise DataValidationError(
                f"{symbol}: provider bar {session} conflicts with official supplement"
            )
        records.append(_verification(supplement, snapshot))
    return [present[session] for session in sorted(present)], tuple(records)


def _parse_supplement(
    row: dict[str, str],
    *,
    path: Path,
    snapshot_date: date,
    cfg: OfficialBarSupplementSection,
) -> OfficialBarSupplement:
    try:
        symbol = row["symbol"].strip()
        session = date.fromisoformat(row["session"].strip())
        bar = Bar(
            symbol=symbol,
            date=session,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            adj_close=float(row["adj_close"]),
            adj_factor=float(row["adj_factor"]),
        )
        supplement = OfficialBarSupplement(
            supplement_id=row["supplement_id"].strip(),
            bar=bar,
            bar_source_url=row["bar_source_url"].strip(),
            bar_source_title=row["bar_source_title"].strip(),
            registrant_cik=row["registrant_cik"].strip(),
            identity_effective_date=date.fromisoformat(
                row["identity_effective_date"].strip()
            ),
            identity_source_url=row["identity_source_url"].strip(),
            identity_source_title=row["identity_source_title"].strip(),
            adjustment_method=row["adjustment_method"].strip(),
            adjustment_previous_session=date.fromisoformat(
                row["adjustment_previous_session"].strip()
            ),
            adjustment_previous_close=float(row["adjustment_previous_close"]),
            adjustment_previous_adj_close=float(
                row["adjustment_previous_adj_close"]
            ),
            adjustment_next_session=date.fromisoformat(
                row["adjustment_next_session"].strip()
            ),
            adjustment_next_close=float(row["adjustment_next_close"]),
            adjustment_next_adj_close=float(row["adjustment_next_adj_close"]),
        )
    except (KeyError, ValueError, PydanticValidationError) as exc:
        raise DataValidationError(f"{path.name}: invalid official-bar row") from exc
    if (
        not supplement.supplement_id
        or not supplement.bar_source_title
        or not supplement.identity_source_title
    ):
        raise DataValidationError(f"{path.name}: official-bar identifiers are required")
    if not _SYMBOL.fullmatch(symbol) or not _CIK.fullmatch(supplement.registrant_cik):
        raise DataValidationError(f"{path.name}: invalid symbol or registrant CIK")
    if session > snapshot_date:
        raise DataValidationError(f"{path.name}: official bar is after snapshot date")
    if supplement.identity_effective_date > session:
        raise DataValidationError(f"{path.name}: security identity is not effective")
    _validate_source(
        supplement.bar_source_url,
        allowed=cfg.allowed_bar_source_hosts,
        label="bar",
        path=path,
    )
    _validate_source(
        supplement.identity_source_url,
        allowed=cfg.allowed_identity_source_hosts,
        label="identity",
        path=path,
    )
    if supplement.adjustment_method != cfg.adjustment_method:
        raise DataValidationError(f"{path.name}: unreviewed adjustment method")
    if not (
        supplement.adjustment_previous_session
        < session
        < supplement.adjustment_next_session
    ):
        raise DataValidationError(
            f"{path.name}: adjustment evidence must bracket the missing session"
        )
    if (
        supplement.adjustment_previous_close
        != supplement.adjustment_previous_adj_close
        or supplement.adjustment_next_close != supplement.adjustment_next_adj_close
        or bar.close != bar.adj_close
        or bar.adj_factor != 1.0
    ):
        raise DataValidationError(
            f"{path.name}: adjacent provider adjustment evidence does not prove factor 1"
        )
    return supplement


def _validate_source(
    url: str,
    *,
    allowed: list[str],
    label: str,
    path: Path,
) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed:
        raise DataValidationError(
            f"{path.name}: {label} source is not an allowed official host"
        )


def _validate_uniqueness(
    supplements: tuple[OfficialBarSupplement, ...],
    path: Path,
) -> None:
    ids: set[str] = set()
    sessions: set[tuple[str, date]] = set()
    for item in supplements:
        if item.supplement_id in ids:
            raise DataValidationError(f"{path.name}: duplicate supplement id")
        ids.add(item.supplement_id)
        key = (item.bar.symbol, item.bar.date)
        if key in sessions:
            raise DataValidationError(
                f"{path.name}: duplicate or conflicting official bar for {key[0]}"
            )
        sessions.add(key)


def _verification(
    supplement: OfficialBarSupplement,
    snapshot: OfficialBarSupplementSnapshot,
) -> OfficialBarSupplementVerification:
    bar = supplement.bar
    return OfficialBarSupplementVerification(
        verification_kind="official_bar_supplement",
        event_id=supplement.supplement_id,
        symbol=bar.symbol,
        session=bar.date,
        raw_open=bar.open,
        raw_high=bar.high,
        raw_low=bar.low,
        raw_close=bar.close,
        raw_volume=bar.volume,
        adj_close=bar.adj_close,
        adj_factor=bar.adj_factor,
        bar_source_url=supplement.bar_source_url,
        registrant_cik=supplement.registrant_cik,
        identity_effective_date=supplement.identity_effective_date,
        identity_source_url=supplement.identity_source_url,
        adjustment_method=supplement.adjustment_method,
        adjustment_previous_session=supplement.adjustment_previous_session,
        adjustment_previous_close=supplement.adjustment_previous_close,
        adjustment_previous_adj_close=supplement.adjustment_previous_adj_close,
        adjustment_next_session=supplement.adjustment_next_session,
        adjustment_next_close=supplement.adjustment_next_close,
        adjustment_next_adj_close=supplement.adjustment_next_adj_close,
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot.sha256,
    )
