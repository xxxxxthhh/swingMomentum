"""Point-in-time official market-event snapshots.

The runtime never scrapes the web.  It selects the newest committed CSV whose
snapshot date is visible at ``as_of`` and validates every row before the data
may waive the otherwise fail-closed volume-spike guard.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from smm.config.schema import VolumeSpikeVerificationSection
from smm.core.errors import DataValidationError

_SNAPSHOT = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})_"
    r"(?P<scope>sp500|index)_constituent_changes\.csv$"
)
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]*$")
_FIELDS = (
    "event_id",
    "source_published_date",
    "effective_date",
    "index_name",
    "action",
    "symbol",
    "source_url",
    "source_title",
)


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_id: str
    source_published_date: date
    effective_date: date
    index_name: str
    action: str
    symbol: str
    source_url: str
    source_title: str


@dataclass(frozen=True, slots=True)
class MarketEventSnapshot:
    snapshot_id: str
    snapshot_date: date
    sha256: str
    events: tuple[MarketEvent, ...]


@dataclass(frozen=True, slots=True)
class VolumeSpikeVerification:
    symbol: str
    session: date
    raw_volume: float
    median_volume: float
    ratio: float
    threshold: float
    event_id: str
    index_name: str
    action: str
    effective_date: date
    source_published_date: date
    source_url: str
    snapshot_id: str
    snapshot_sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "action": self.action,
            "effective_date": self.effective_date.isoformat(),
            "event_id": self.event_id,
            "index_name": self.index_name,
            "median_volume": f"{self.median_volume:.6f}",
            "ratio": f"{self.ratio:.6f}",
            "raw_volume": f"{self.raw_volume:.6f}",
            "session": self.session.isoformat(),
            "snapshot_id": self.snapshot_id,
            "snapshot_sha256": self.snapshot_sha256,
            "source_published_date": self.source_published_date.isoformat(),
            "source_url": self.source_url,
            "symbol": self.symbol,
            "threshold": f"{self.threshold:.6f}",
            "verification_kind": "volume_spike",
        }


def load_market_event_snapshot(
    directory: Path | str,
    *,
    as_of: date,
    cfg: VolumeSpikeVerificationSection,
) -> MarketEventSnapshot:
    """Select and validate the newest committed snapshot visible at ``as_of``."""
    candidates: list[tuple[date, int, Path]] = []
    for path in Path(directory).glob("*_constituent_changes.csv"):
        match = _SNAPSHOT.fullmatch(path.name)
        if not match:
            continue
        try:
            snapshot_date = date.fromisoformat(match.group("day"))
        except ValueError:
            continue
        if snapshot_date <= as_of:
            candidates.append(
                (snapshot_date, int(match.group("scope") == "index"), path)
            )
    if not candidates:
        raise DataValidationError(f"no market-event snapshot is visible at {as_of}")

    snapshot_date, _, path = max(
        candidates,
        key=lambda item: (item[0], item[1], item[2].name),
    )
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataValidationError(f"{path.name}: market-event snapshot is not UTF-8") from exc

    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != _FIELDS:
        raise DataValidationError(
            f"{path.name}: market-event columns must be exactly {', '.join(_FIELDS)}"
        )
    events = tuple(_parse_event(row, path, snapshot_date, cfg) for row in reader)
    if not events:
        raise DataValidationError(f"{path.name}: market-event snapshot is empty")
    _validate_uniqueness(events, path)
    return MarketEventSnapshot(
        snapshot_id=path.stem,
        snapshot_date=snapshot_date,
        sha256=hashlib.sha256(payload).hexdigest(),
        events=tuple(sorted(events, key=lambda item: item.event_id)),
    )


def match_market_event(
    snapshot: MarketEventSnapshot,
    *,
    symbol: str,
    spike_session: date,
    calendar: list[date],
) -> MarketEvent:
    """Return the unique exact T-1/T event that authorizes this anomaly."""
    sessions = sorted(set(calendar))
    matches: list[MarketEvent] = []
    for event in snapshot.events:
        if event.symbol != symbol.upper() or event.source_published_date > spike_session:
            continue
        previous = max(
            (session for session in sessions if session < event.effective_date),
            default=None,
        )
        if spike_session in {previous, event.effective_date}:
            matches.append(event)
    if len(matches) != 1:
        raise DataValidationError(
            f"{symbol.upper()}: {spike_session} volume spike has no unique official "
            "index constituent-change event in the T-1/T window"
        )
    return matches[0]


def _parse_event(
    row: dict[str, str],
    path: Path,
    snapshot_date: date,
    cfg: VolumeSpikeVerificationSection,
) -> MarketEvent:
    try:
        event_id = row["event_id"].strip()
        published = date.fromisoformat(row["source_published_date"].strip())
        effective = date.fromisoformat(row["effective_date"].strip())
        index_name = row["index_name"].strip()
        action = row["action"].strip()
        symbol = row["symbol"].strip()
        source_url = row["source_url"].strip()
        source_title = row["source_title"].strip()
    except (KeyError, ValueError) as exc:
        raise DataValidationError(f"{path.name}: invalid market-event row") from exc
    if not event_id or not source_title:
        raise DataValidationError(f"{path.name}: event_id and source_title are required")
    if published > snapshot_date:
        raise DataValidationError(f"{path.name}: event publication is after snapshot date")
    if effective < published:
        raise DataValidationError(f"{path.name}: event effective date precedes publication")
    if index_name not in cfg.allowed_indexes or action not in cfg.allowed_actions:
        raise DataValidationError(f"{path.name}: event is outside the frozen catalog")
    if not _SYMBOL.fullmatch(symbol):
        raise DataValidationError(f"{path.name}: invalid exact symbol {symbol!r}")
    parsed_url = urlparse(source_url)
    allowed_hosts = cfg.allowed_source_hosts_by_index[index_name]
    if parsed_url.scheme != "https" or parsed_url.hostname not in allowed_hosts:
        raise DataValidationError(
            f"{path.name}: event source host is not allowed for {index_name}"
        )
    return MarketEvent(
        event_id=event_id,
        source_published_date=published,
        effective_date=effective,
        index_name=index_name,
        action=action,
        symbol=symbol,
        source_url=source_url,
        source_title=source_title,
    )


def _validate_uniqueness(events: tuple[MarketEvent, ...], path: Path) -> None:
    event_ids: set[str] = set()
    business_keys: dict[tuple[str, str, date], str] = {}
    for event in events:
        if event.event_id in event_ids:
            raise DataValidationError(f"{path.name}: duplicate market event id")
        event_ids.add(event.event_id)
        key = (event.index_name, event.symbol, event.effective_date)
        prior = business_keys.get(key)
        if prior is not None:
            kind = "duplicate" if prior == event.action else "conflicting"
            raise DataValidationError(f"{path.name}: {kind} market event for {event.symbol}")
        business_keys[key] = event.action
