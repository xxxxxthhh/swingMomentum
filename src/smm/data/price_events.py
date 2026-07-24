"""Point-in-time EDGAR price events and independent security identities.

Runtime verification is deliberately offline.  It selects committed snapshots
visible at ``as_of``, validates their exact catalog, and only then permits a
genuine extreme price move to pass the otherwise fail-closed 50% guard.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from smm.config.schema import PriceJumpVerificationSection
from smm.core.errors import DataValidationError

_EVENT_SNAPSHOT = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})_edgar_item_1_01\.csv$")
_IDENTITY_SNAPSHOT = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})_symbol_mappings\.csv$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]*$")
_CIK = re.compile(r"^\d{10}$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_EVENT_FIELDS = (
    "event_id",
    "registrant_cik",
    "accession_number",
    "form",
    "item_number",
    "acceptance_datetime",
    "historical_symbol",
    "security_class",
    "source_url",
    "source_title",
)
_IDENTITY_FIELDS = (
    "mapping_id",
    "registrant_cik",
    "security_class",
    "old_symbol",
    "new_symbol",
    "effective_date",
    "source_published_at",
    "source_url",
    "source_title",
    "cusip_continuity",
    "before_edgar_url",
    "after_edgar_url",
)


@dataclass(frozen=True, slots=True)
class PriceEvent:
    event_id: str
    registrant_cik: str
    accession_number: str
    form: str
    item_number: str
    acceptance_datetime: datetime
    historical_symbol: str
    security_class: str
    source_url: str
    source_title: str


@dataclass(frozen=True, slots=True)
class PriceEventSnapshot:
    snapshot_id: str
    snapshot_date: date
    sha256: str
    exchange_timezone: str
    regular_close: time
    events: tuple[PriceEvent, ...]


@dataclass(frozen=True, slots=True)
class SecurityIdentityMapping:
    mapping_id: str
    registrant_cik: str
    security_class: str
    old_symbol: str
    new_symbol: str
    effective_date: date
    source_published_at: datetime
    source_url: str
    source_title: str
    cusip_continuity: str
    before_edgar_url: str
    after_edgar_url: str


@dataclass(frozen=True, slots=True)
class SecurityIdentitySnapshot:
    snapshot_id: str
    snapshot_date: date
    sha256: str
    mappings: tuple[SecurityIdentityMapping, ...]


@dataclass(frozen=True, slots=True)
class PriceJumpVerification:
    verification_kind: str
    symbol: str
    historical_symbol: str
    session: date
    previous_close: float
    raw_close: float
    move: float
    threshold: float
    event_id: str
    registrant_cik: str
    accession_number: str
    form: str
    item_number: str
    acceptance_datetime: datetime
    eligible_session: date
    source_url: str
    price_event_snapshot_id: str
    price_event_snapshot_sha256: str
    identity_mapping_id: str | None
    identity_source_url: str | None
    security_identity_snapshot_id: str
    security_identity_snapshot_sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "acceptance_datetime": self.acceptance_datetime.isoformat(),
            "accession_number": self.accession_number,
            "eligible_session": self.eligible_session.isoformat(),
            "event_id": self.event_id,
            "form": self.form,
            "historical_symbol": self.historical_symbol,
            "identity_mapping_id": self.identity_mapping_id,
            "identity_source_url": self.identity_source_url,
            "item_number": self.item_number,
            "move": f"{self.move:.12f}",
            "previous_close": f"{self.previous_close:.6f}",
            "price_event_snapshot_id": self.price_event_snapshot_id,
            "price_event_snapshot_sha256": self.price_event_snapshot_sha256,
            "raw_close": f"{self.raw_close:.6f}",
            "registrant_cik": self.registrant_cik,
            "security_identity_snapshot_id": self.security_identity_snapshot_id,
            "security_identity_snapshot_sha256": self.security_identity_snapshot_sha256,
            "session": self.session.isoformat(),
            "source_url": self.source_url,
            "symbol": self.symbol,
            "threshold": f"{self.threshold:.6f}",
            "verification_kind": self.verification_kind,
        }


def load_price_event_snapshot(
    directory: Path | str,
    *,
    as_of: date,
    cfg: PriceJumpVerificationSection,
) -> PriceEventSnapshot:
    snapshot_date, path = _select_snapshot(
        Path(directory), as_of=as_of, pattern=_EVENT_SNAPSHOT
    )
    payload, reader = _read_snapshot(path, _EVENT_FIELDS, "price-event")
    events = tuple(_parse_event(row, path, snapshot_date, cfg) for row in reader)
    if not events:
        raise DataValidationError(f"{path.name}: price-event snapshot is empty")
    _unique(events, path, "event_id")
    return PriceEventSnapshot(
        snapshot_id=path.stem,
        snapshot_date=snapshot_date,
        sha256=hashlib.sha256(payload).hexdigest(),
        exchange_timezone=cfg.exchange_timezone,
        regular_close=time.fromisoformat(cfg.regular_close),
        events=tuple(sorted(events, key=lambda item: item.event_id)),
    )


def load_security_identity_snapshot(
    directory: Path | str,
    *,
    as_of: date,
    cfg: PriceJumpVerificationSection,
) -> SecurityIdentitySnapshot:
    snapshot_date, path = _select_snapshot(
        Path(directory), as_of=as_of, pattern=_IDENTITY_SNAPSHOT
    )
    payload, reader = _read_snapshot(path, _IDENTITY_FIELDS, "security-identity")
    mappings = tuple(_parse_mapping(row, path, snapshot_date, cfg) for row in reader)
    if not mappings:
        raise DataValidationError(f"{path.name}: security-identity snapshot is empty")
    _unique(mappings, path, "mapping_id")
    keys: set[tuple[str, str, str, str, date]] = set()
    for mapping in mappings:
        key = (
            mapping.registrant_cik,
            mapping.security_class,
            mapping.old_symbol,
            mapping.new_symbol,
            mapping.effective_date,
        )
        if key in keys:
            raise DataValidationError(f"{path.name}: duplicate security identity mapping")
        keys.add(key)
    return SecurityIdentitySnapshot(
        snapshot_id=path.stem,
        snapshot_date=snapshot_date,
        sha256=hashlib.sha256(payload).hexdigest(),
        mappings=tuple(sorted(mappings, key=lambda item: item.mapping_id)),
    )


def match_price_event(
    events: PriceEventSnapshot,
    identities: SecurityIdentitySnapshot,
    *,
    current_symbol: str,
    jump_session: date,
    calendar: list[date],
) -> tuple[PriceEvent, SecurityIdentityMapping | None]:
    """Return the unique event whose earliest eligible session is the jump."""
    symbol = current_symbol.upper()
    sessions = sorted(set(calendar))
    if not sessions:
        raise DataValidationError("price jump cannot be verified without a trading calendar")
    matches: list[tuple[PriceEvent, SecurityIdentityMapping | None]] = []
    for event in events.events:
        eligible = _eligible_session(
            event.acceptance_datetime,
            sessions,
            exchange_timezone=events.exchange_timezone,
            regular_close=events.regular_close,
        )
        if eligible != jump_session:
            continue
        if event.historical_symbol == symbol:
            matches.append((event, None))
            continue
        mappings = [
            mapping
            for mapping in identities.mappings
            if mapping.old_symbol == event.historical_symbol
            and mapping.new_symbol == symbol
            and mapping.registrant_cik == event.registrant_cik
            and mapping.security_class == event.security_class
            and mapping.cusip_continuity == "unchanged"
        ]
        if len(mappings) != 1:
            continue
        matches.append((event, mappings[0]))
    if len(matches) != 1:
        identity_matches = [
            mapping
            for mapping in identities.mappings
            if mapping.new_symbol == symbol
        ]
        if not identity_matches and any(
            event.historical_symbol != symbol for event in events.events
        ):
            raise DataValidationError(
                f"{symbol}: {jump_session} has no unique security identity mapping"
            )
        raise DataValidationError(
            f"{symbol}: {jump_session} price jump has no unique EDGAR Form 8-K "
            "Item 1.01 event on the exact eligible session"
        )
    return matches[0]


def eligible_session(
    accepted: datetime,
    calendar: list[date],
    *,
    exchange_timezone: str,
    regular_close: time,
) -> date:
    """Public deterministic session rule used in evidence construction."""
    return _eligible_session(
        accepted,
        sorted(set(calendar)),
        exchange_timezone=exchange_timezone,
        regular_close=regular_close,
    )


def _select_snapshot(
    directory: Path,
    *,
    as_of: date,
    pattern: re.Pattern[str],
) -> tuple[date, Path]:
    candidates: list[tuple[date, Path]] = []
    for path in directory.glob("*.csv"):
        match = pattern.fullmatch(path.name)
        if match is None:
            continue
        try:
            snapshot_date = date.fromisoformat(match.group("day"))
        except ValueError:
            continue
        if snapshot_date <= as_of:
            candidates.append((snapshot_date, path))
    if not candidates:
        raise DataValidationError(f"no matching snapshot is visible at {as_of}")
    return max(candidates, key=lambda item: (item[0], item[1].name))


def _read_snapshot(
    path: Path,
    fields: tuple[str, ...],
    label: str,
) -> tuple[bytes, csv.DictReader]:
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataValidationError(f"{path.name}: {label} snapshot is not UTF-8") from exc
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != fields:
        raise DataValidationError(
            f"{path.name}: {label} columns must be exactly {', '.join(fields)}"
        )
    return payload, reader


def _parse_event(
    row: dict[str, str],
    path: Path,
    snapshot_date: date,
    cfg: PriceJumpVerificationSection,
) -> PriceEvent:
    try:
        event = PriceEvent(
            event_id=row["event_id"].strip(),
            registrant_cik=row["registrant_cik"].strip(),
            accession_number=row["accession_number"].strip(),
            form=row["form"].strip(),
            item_number=row["item_number"].strip(),
            acceptance_datetime=datetime.fromisoformat(
                row["acceptance_datetime"].strip()
            ),
            historical_symbol=row["historical_symbol"].strip(),
            security_class=row["security_class"].strip(),
            source_url=row["source_url"].strip(),
            source_title=row["source_title"].strip(),
        )
    except (KeyError, ValueError) as exc:
        raise DataValidationError(f"{path.name}: invalid price-event row") from exc
    if (
        not event.event_id
        or not event.security_class
        or not event.source_title
        or event.acceptance_datetime.tzinfo is None
    ):
        raise DataValidationError(f"{path.name}: incomplete price-event row")
    if event.acceptance_datetime.date() > snapshot_date:
        raise DataValidationError(f"{path.name}: EDGAR acceptance is after snapshot date")
    if event.form not in cfg.allowed_forms or event.item_number not in cfg.allowed_items:
        raise DataValidationError(f"{path.name}: event is outside the frozen EDGAR catalog")
    if not _CIK.fullmatch(event.registrant_cik) or not _ACCESSION.fullmatch(
        event.accession_number
    ):
        raise DataValidationError(f"{path.name}: invalid CIK or accession number")
    if not _SYMBOL.fullmatch(event.historical_symbol):
        raise DataValidationError(f"{path.name}: invalid historical symbol")
    _allowed_url(event.source_url, cfg.allowed_source_hosts, path, "EDGAR event")
    return event


def _parse_mapping(
    row: dict[str, str],
    path: Path,
    snapshot_date: date,
    cfg: PriceJumpVerificationSection,
) -> SecurityIdentityMapping:
    try:
        mapping = SecurityIdentityMapping(
            mapping_id=row["mapping_id"].strip(),
            registrant_cik=row["registrant_cik"].strip(),
            security_class=row["security_class"].strip(),
            old_symbol=row["old_symbol"].strip(),
            new_symbol=row["new_symbol"].strip(),
            effective_date=date.fromisoformat(row["effective_date"].strip()),
            source_published_at=datetime.fromisoformat(
                row["source_published_at"].strip()
            ),
            source_url=row["source_url"].strip(),
            source_title=row["source_title"].strip(),
            cusip_continuity=row["cusip_continuity"].strip(),
            before_edgar_url=row["before_edgar_url"].strip(),
            after_edgar_url=row["after_edgar_url"].strip(),
        )
    except (KeyError, ValueError) as exc:
        raise DataValidationError(f"{path.name}: invalid security-identity row") from exc
    if (
        not mapping.mapping_id
        or not mapping.security_class
        or not mapping.source_title
        or mapping.source_published_at.tzinfo is None
    ):
        raise DataValidationError(f"{path.name}: incomplete security-identity row")
    if mapping.source_published_at.date() > snapshot_date:
        raise DataValidationError(f"{path.name}: identity source is after snapshot date")
    if mapping.cusip_continuity != "unchanged":
        raise DataValidationError(f"{path.name}: identity must prove unchanged CUSIP")
    if not _CIK.fullmatch(mapping.registrant_cik):
        raise DataValidationError(f"{path.name}: invalid identity CIK")
    if not _SYMBOL.fullmatch(mapping.old_symbol) or not _SYMBOL.fullmatch(
        mapping.new_symbol
    ):
        raise DataValidationError(f"{path.name}: invalid identity symbol")
    _allowed_url(
        mapping.source_url,
        cfg.allowed_identity_source_hosts,
        path,
        "identity source",
    )
    _allowed_url(mapping.before_edgar_url, ["www.sec.gov"], path, "before EDGAR")
    _allowed_url(mapping.after_edgar_url, ["www.sec.gov"], path, "after EDGAR")
    return mapping


def _eligible_session(
    accepted: datetime,
    sessions: list[date],
    *,
    exchange_timezone: str,
    regular_close: time,
) -> date:
    if accepted.tzinfo is None:
        raise DataValidationError("EDGAR acceptance datetime must be timezone-aware")
    eastern = accepted.astimezone(ZoneInfo(exchange_timezone))
    if (
        eastern.date() in sessions
        and eastern.time().replace(tzinfo=None) <= regular_close
    ):
        return eastern.date()
    following = next((session for session in sessions if session > eastern.date()), None)
    if following is None:
        raise DataValidationError("EDGAR acceptance has no following provider session")
    return following


def _allowed_url(
    value: str,
    hosts: list[str],
    path: Path,
    label: str,
) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in hosts:
        raise DataValidationError(f"{path.name}: {label} is not on an allowed host")


def _unique(items: tuple[object, ...], path: Path, attribute: str) -> None:
    values = [getattr(item, attribute) for item in items]
    if len(values) != len(set(values)):
        raise DataValidationError(f"{path.name}: duplicate {attribute}")
