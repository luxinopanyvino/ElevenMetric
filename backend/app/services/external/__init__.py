"""External data sources: import a real team instead of typing one in.

Two adapters, answering different questions and never allowed to blur together:

* :mod:`sofifa` — club squads and player ratings from the EA Sports FC video
  game. Real clubs, real names, and attributes that are *a games studio's
  opinion*. Tier 1.
* :mod:`statsbomb` — real fixtures, lineups and on-ball events from StatsBomb's
  free open data. Real observations, and **no ratings at all**. Tier 2.

Both resolve to the shapes in :mod:`base`, so preview, provenance, error
reporting and commit are written once (:mod:`commit`).

Nothing here is a runtime dependency of the API. With `statsbombpy` absent and
outbound fetching switched off — the defaults — the API starts normally, every
existing route behaves identically, and :func:`capabilities` reports each source
as unavailable together with the reason and the remedy.
"""

from __future__ import annotations

from app.services.external import sofifa, statsbomb
from app.services.external.base import (
    ExternalSourceError,
    FetchError,
    SourceCapability,
    SourceFixture,
    SourcePlayer,
    SourceSquad,
    SourceUnavailable,
)

SOURCES = {
    sofifa.SOURCE: sofifa,
    statsbomb.SOURCE: statsbomb,
}


def capabilities() -> list[dict]:
    """What each source is, and whether it can be used right now."""
    return [module.capability().to_dict() for module in SOURCES.values()]


__all__ = [
    "SOURCES",
    "ExternalSourceError",
    "FetchError",
    "SourceCapability",
    "SourceFixture",
    "SourcePlayer",
    "SourceSquad",
    "SourceUnavailable",
    "capabilities",
    "sofifa",
    "statsbomb",
]
