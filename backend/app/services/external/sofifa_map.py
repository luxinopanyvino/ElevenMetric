"""SoFIFA's vocabulary → this product's vocabulary.

Both tables are **exact match only**. An unrecognised position makes its row
unmappable and the row is reported with the offending value; an unrecognised
attribute is reported and dropped. Nothing is matched by similarity, because a
near-miss mapping is indistinguishable from a correct one once it is in the
database — the same rule the CSV route already follows.

The two vocabularies line up almost perfectly, which is not a coincidence: this
product's attribute layering was modelled on the same scouting vocabulary the
game uses. See `app/services/ml/features.py`.
"""

from __future__ import annotations

from app.models.catalog import Position
from app.services.ml.features import ATTRIBUTE_KEYS

#: SoFIFA position code → our Position. SoFIFA does not distinguish left from
#: right centre-back (we have RCB/LCB) nor publish a second-striker code, so
#: those of ours simply have no source equivalent — which is fine, they are
#: reachable through the by-hand editor and through positional fit.
POSITION_MAP: dict[str, Position] = {
    "GK": Position.GK,
    "CB": Position.CB,
    "RCB": Position.RCB,
    "LCB": Position.LCB,
    "RB": Position.RB,
    "LB": Position.LB,
    "RWB": Position.RWB,
    "LWB": Position.LWB,
    "CDM": Position.DM,
    "RDM": Position.DM,
    "LDM": Position.DM,
    "CM": Position.CM,
    "RCM": Position.CM,
    "LCM": Position.CM,
    "CAM": Position.AM,
    "RAM": Position.AM,
    "LAM": Position.AM,
    "RM": Position.RM,
    "LM": Position.LM,
    "RW": Position.RW,
    "LW": Position.LW,
    "CF": Position.CF,
    "RF": Position.CF,
    "LF": Position.CF,
    "ST": Position.ST,
    "RS": Position.ST,
    "LS": Position.ST,
    "SUB": None,      # a squad-list marker, not a position
    "RES": None,
}

#: SoFIFA attribute label → our attribute key. Keys are normalised through
#: :func:`normalise` first, so "Sprint speed", "sprint_speed" and
#: "movement_sprint_speed" all land on the same entry.
ATTRIBUTE_MAP: dict[str, str] = {
    # Headline faces. SoFIFA calls the physical face "physic" in its exports.
    "pace": "pace",
    "shooting": "shooting",
    "passing": "passing",
    "dribbling": "dribbling",
    "defending": "defending",
    "physic": "physical",
    "physical": "physical",
    "physicality": "physical",

    # Attacking
    "crossing": "crossing",
    "finishing": "finishing",
    "headingaccuracy": "heading_accuracy",
    "shortpassing": "short_passing",
    "volleys": "volleys",

    # Skill
    "curve": "curve",
    "fkaccuracy": "free_kick_accuracy",
    "freekickaccuracy": "free_kick_accuracy",
    "longpassing": "long_passing",
    "ballcontrol": "ball_control",

    # Movement
    "acceleration": "acceleration",
    "sprintspeed": "sprint_speed",
    "agility": "agility",
    "reactions": "reactions",
    "balance": "balance",

    # Power
    "shotpower": "shot_power",
    "jumping": "jumping",
    "stamina": "stamina",
    "strength": "strength",
    "longshots": "long_shots",

    # Mentality
    "aggression": "aggression",
    "interceptions": "interceptions",
    "vision": "vision",
    "penalties": "penalties",
    "composure": "composure",

    # Defending
    "markingawareness": "defensive_awareness",
    "defensiveawareness": "defensive_awareness",
    "marking": "defensive_awareness",
    "standingtackle": "standing_tackle",
    "slidingtackle": "sliding_tackle",

    # Goalkeeping
    "gkdiving": "gk_diving",
    "gkhandling": "gk_handling",
    "gkkicking": "gk_kicking",
    "gkpositioning": "gk_positioning",
    "gkreflexes": "gk_reflexes",
    "gkspeed": "gk_speed",
    # Work rates arrive already split by `parse_work_rate`, so the mapper has to
    # recognise its own output as well as the source's labels.
    "workrateoff": "work_rate_off",
    "workratedef": "work_rate_def",
    "attackingworkrate": "work_rate_off",
    "defensiveworkrate": "work_rate_def",

    "diving": "gk_diving",
    "handling": "gk_handling",
    "kicking": "gk_kicking",
    "reflexes": "gk_reflexes",
    "speed": "gk_speed",
}

#: Prefixes SoFIFA's bulk exports put in front of attribute names, e.g.
#: ``attacking_finishing``, ``goalkeeping_diving``. Stripped before lookup so
#: one table serves both the page markup and the export columns.
_EXPORT_PREFIXES = (
    "attacking", "skill", "movement", "power", "mentality", "defending",
    "goalkeeping",
)

#: SoFIFA's `mentality_positioning` is *attacking* positioning, which this
#: product does not yet carry as its own key (spec 001 adds it). Until then it
#: has no home and must be reported as unmapped rather than folded into
#: `defensive_awareness`, which is a different skill entirely.
KNOWN_UNMAPPED: dict[str, str] = {
    "positioning": "attacking positioning has no key in this product's "
                   "vocabulary yet — see spec 001",
    "mentalitypositioning": "attacking positioning has no key in this product's "
                            "vocabulary yet — see spec 001",
}


def normalise(label: str) -> str:
    """Lower-case and strip everything that is not a letter or digit."""
    return "".join(ch for ch in label.strip().lower() if ch.isalnum())


def map_attribute(label: str) -> str | None:
    """Our attribute key for a source label, or ``None`` if there is none."""
    key = normalise(label)
    if (hit := ATTRIBUTE_MAP.get(key)) is not None:
        return hit
    # `goalkeeping_diving` → `gkdiving`; `attacking_finishing` → `finishing`.
    for prefix in _EXPORT_PREFIXES:
        if key.startswith(prefix) and len(key) > len(prefix):
            rest = key[len(prefix):]
            if prefix == "goalkeeping":
                rest = "gk" + rest
            if (hit := ATTRIBUTE_MAP.get(rest)) is not None:
                return hit
    return None


def map_attributes(raw: dict[str, float]) -> tuple[dict[str, float], list[dict]]:
    """Map a source attribute blob, reporting what could not be placed.

    Returns ``(mapped, unmapped)``. ``unmapped`` entries carry the source label
    and the reason, so the preview can show them rather than the import quietly
    losing data.
    """
    mapped: dict[str, float] = {}
    unmapped: list[dict] = []
    known = set(ATTRIBUTE_KEYS)

    for label, value in raw.items():
        key = map_attribute(label)
        if key is None or key not in known:
            reason = KNOWN_UNMAPPED.get(
                normalise(label),
                "no attribute in this product's vocabulary corresponds to it",
            )
            unmapped.append({"source_label": label, "value": value, "reason": reason})
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            unmapped.append({"source_label": label, "value": value,
                             "reason": "not a number"})
            continue
        mapped[key] = max(0.0, min(99.0, number))

    return mapped, unmapped


def map_positions(raw: str) -> tuple[list[Position], list[str]]:
    """Parse a source position string into our positions, plus what failed.

    SoFIFA publishes ``"ST, CF"``, ``"CDM CM"`` and single codes. The first
    recognised code is the player's primary position; the rest are secondary.
    """
    tokens = [t.strip().upper()
              for t in raw.replace("|", ",").replace("/", ",").replace(" ", ",").split(",")
              if t.strip()]
    positions: list[Position] = []
    unknown: list[str] = []
    for token in tokens:
        if token in POSITION_MAP:
            if (position := POSITION_MAP[token]) is not None and position not in positions:
                positions.append(position)
        else:
            unknown.append(token)
    return positions, unknown


def parse_work_rate(raw: str) -> dict[str, float]:
    """SoFIFA's ``"High/Medium"`` → this product's 0-99 work-rate pair.

    The three published levels are mapped onto the scale the rest of the
    product uses. There is no finer signal in the source to preserve.
    """
    levels = {"low": 30.0, "medium": 60.0, "high": 85.0, "med": 60.0}
    parts = [p.strip().lower() for p in raw.replace("\\", "/").split("/") if p.strip()]
    out: dict[str, float] = {}
    if parts and parts[0] in levels:
        out["work_rate_off"] = levels[parts[0]]
    if len(parts) > 1 and parts[1] in levels:
        out["work_rate_def"] = levels[parts[1]]
    return out
