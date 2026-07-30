"""Shared vocabulary for external data sources.

Every adapter in this package resolves to the same two shapes — :class:`SourceSquad`
and :class:`SourceFixture` — so preview, error reporting, provenance and the
commit path are written once and behave identically no matter where the data
came from.

The types deliberately model the source *as published*, before any mapping into
this product's vocabulary. That is what lets a preview show the user which
decisions were made ("SoFIFA said ``CDM``, we stored ``DM``") rather than hiding
them behind an already-mapped row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


# --- Errors ----------------------------------------------------------------

class ExternalSourceError(Exception):
    """Base class for everything that can go wrong reaching a source."""


class SourceUnavailable(ExternalSourceError):
    """The source cannot be used at all right now.

    Carries the remedy, because "unavailable" without "here is how to fix it" is
    the failure mode this project's optional-dependency rule exists to prevent.
    """

    def __init__(self, source: str, reason: str, remedy: str) -> None:
        super().__init__(f"{source} is unavailable: {reason}")
        self.source = source
        self.reason = reason
        self.remedy = remedy


class FetchError(ExternalSourceError):
    """A request or a parse failed.

    Names what was expected, so a source that changes its markup produces a
    report a maintainer can act on rather than a bare 500. Nothing is written
    when this is raised — a half-imported squad is worse than none.
    """

    def __init__(self, source: str, url: str, expected: str, detail: str = "") -> None:
        message = f"{source}: could not read {url} — expected {expected}"
        if detail:
            message += f" ({detail})"
        super().__init__(message)
        self.source = source
        self.url = url
        self.expected = expected
        self.detail = detail


# --- Provenance ------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def provenance(
    *,
    source: str,
    edition: str,
    source_id: str,
    source_url: str | None = None,
    retrieved: str = "fetch",
    note: str = "",
) -> dict:
    """Build the record that travels with every imported row.

    ``retrieved`` is ``"fetch"`` (live request) or ``"file"`` (a page or export
    the user supplied), so an audit can tell a live read from a replay.

    This is the mechanism behind the constitution's fourth principle. It is a
    column on `teams`, `players` and `matches`, not a log line, because a log
    can be rotated away while the claim it justified stays on screen.
    """
    return {
        "source": source,
        "edition": edition,
        "source_id": str(source_id),
        "source_url": source_url,
        "retrieved": retrieved,
        "retrieved_at": utc_now_iso(),
        "note": note,
    }


# --- Source shapes ---------------------------------------------------------

@dataclass
class SourcePlayer:
    """A player exactly as a source publishes them, before mapping."""

    source_id: str
    name: str
    #: Raw position string(s) as published — "CDM", "ST, CF", "Goalkeeper"…
    position_raw: str = ""
    known_as: str = ""
    shirt_number: int | None = None
    age: int | None = None
    birth_date: date | None = None
    nationality: str | None = None
    #: `None` means the source published no rating. It does not mean average.
    overall: float | None = None
    potential: float | None = None
    #: Source attribute label → value, unmapped.
    attributes_raw: dict[str, float] = field(default_factory=dict)
    preferred_foot: str | None = None
    height_cm: int | None = None
    weight_kg: int | None = None
    market_value_eur: int | None = None
    wage_eur_per_year: int | None = None
    contract_until: date | None = None


@dataclass
class SourceSquad:
    """A club and its players as a source publishes them."""

    source_id: str
    name: str
    league: str | None = None
    country: str | None = None
    formation: str | None = None
    players: list[SourcePlayer] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


@dataclass
class SourceEvent:
    """One on-ball action, in the source's own coordinate frame."""

    source_id: str
    team: str
    player: str | None
    period: int
    minute: int
    second: int
    type: str
    outcome: str = "success"
    x: float = 0.0
    y: float = 0.0
    end_x: float | None = None
    end_y: float | None = None
    qualifiers: dict = field(default_factory=dict)


@dataclass
class SourceFixture:
    """A real match as a source publishes it."""

    source_id: str
    competition: str
    season: str
    kickoff: datetime | None
    home: str
    away: str
    score: tuple[int, int] = (0, 0)
    #: team name → the players who appeared for them.
    lineups: dict[str, list[SourcePlayer]] = field(default_factory=dict)
    events: list[SourceEvent] = field(default_factory=list)
    #: Coordinate frame these events are in — a key of `PROVIDER_FRAMES`.
    frame: str = "statsbomb"
    provenance: dict = field(default_factory=dict)


# --- Capability reporting --------------------------------------------------

@dataclass
class SourceCapability:
    """What a source is, and whether it can be used right now.

    Served from ``GET /api/v1/external/sources`` and rendered in the UI, so the
    panel can say *why* a source is greyed out instead of simply failing when
    the user clicks.
    """

    key: str
    label: str
    #: What the source actually is, in the user's terms. The place where the
    #: product refuses to let "game ratings" and "real match data" blur.
    what_it_is: str
    #: Which input tier the imported data lands in (1 = squad, 2 = events).
    tier: int
    supplies: list[str]
    does_not_supply: list[str]
    available: bool
    reason: str = ""
    remedy: str = ""
    attribution: str = ""
    terms_url: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "what_it_is": self.what_it_is,
            "tier": self.tier,
            "supplies": self.supplies,
            "does_not_supply": self.does_not_supply,
            "available": self.available,
            "reason": self.reason,
            "remedy": self.remedy,
            "attribution": self.attribution,
            "terms_url": self.terms_url,
        }
