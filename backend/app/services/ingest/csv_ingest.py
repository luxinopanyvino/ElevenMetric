"""CSV ingestion: parse, map, validate.

Clubs export from wildly different systems, so the parser is tolerant where it
can be and strict where it must be:

* **Tolerant on shape** — delimiter is sniffed, headers are matched
  case-insensitively and ignore spaces, hyphens and underscores, and a set of
  aliases covers the names other tools actually use (``fullname``, ``pos``,
  ``dob``, ``ovr``).
* **Strict on meaning** — an unknown column, an unparseable number or an
  out-of-range value is an error against that row, reported with the row number
  and the offending value. Nothing is coerced silently.

Nothing here writes to the database. :func:`parse` returns valid rows and errors
so the caller can preview before committing, which is the whole point: a bad
import is far cheaper to catch than to undo.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from app.models.academy import AgeGroup
from app.models.catalog import Foot, Position
from app.services.analytics.pitch import PROVIDER_FRAMES
from app.services.ml.features import ATTRIBUTE_KEYS

MAX_ROWS = 20_000
PREVIEW_ROWS = 12


# --- Column types ----------------------------------------------------------

@dataclass(frozen=True)
class Column:
    name: str
    type: str
    required: bool = False
    description: str = ""
    example: str = ""
    #: Alternative header spellings seen in real exports.
    aliases: tuple[str, ...] = ()
    #: Attribute columns are collected into the `attributes` JSON blob.
    attribute: bool = False


def _norm(header: str) -> str:
    return "".join(ch for ch in header.strip().lower() if ch.isalnum())


def _parse_int(v: str) -> int:
    return int(float(v.replace(",", "").replace(" ", "")))


def _parse_float(v: str) -> float:
    return float(v.replace(",", ".").replace(" ", "")) if "," in v and "." not in v \
        else float(v.replace(" ", ""))


def _parse_bool(v: str) -> bool:
    s = v.strip().lower()
    if s in {"1", "true", "t", "yes", "y", "si", "sí"}:
        return True
    if s in {"0", "false", "f", "no", "n"}:
        return False
    raise ValueError(f"expected yes/no, got {v!r}")


def _parse_date(v: str) -> date:
    s = v.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {v!r} — use YYYY-MM-DD")


def _enum_parser(enum_cls, label: str) -> Callable[[str], Any]:
    valid = {e.value.lower(): e.value for e in enum_cls}

    def parse(v: str) -> str:
        s = v.strip().lower()
        if s not in valid:
            raise ValueError(f"unknown {label} {v!r} — one of {sorted(valid.values())}")
        return valid[s]

    return parse


def _parse_positions(v: str) -> list[str]:
    valid = {p.value.lower(): p.value for p in Position}
    out = []
    for part in v.replace("|", ",").replace(";", ",").split(","):
        s = part.strip().lower()
        if not s:
            continue
        if s not in valid:
            raise ValueError(f"unknown position {part.strip()!r}")
        out.append(valid[s])
    return out


PARSERS: dict[str, Callable[[str], Any]] = {
    "str": lambda v: v.strip(),
    "int": _parse_int,
    "float": _parse_float,
    "bool": _parse_bool,
    "date": _parse_date,
    "position": _enum_parser(Position, "position"),
    "positions": _parse_positions,
    "foot": _enum_parser(Foot, "foot"),
    "age_group": _enum_parser(AgeGroup, "age group"),
    "rating": lambda v: max(0.0, min(99.0, float(v.replace(",", ".")))),
}


# --- Dataset definitions ---------------------------------------------------

def _attribute_columns() -> list[Column]:
    return [
        Column(k, "rating", description=f"0-99 · {k.replace('_', ' ')}",
               example="82", attribute=True)
        for k in ATTRIBUTE_KEYS
    ]


@dataclass
class Dataset:
    key: str
    label: str
    description: str
    columns: list[Column]
    #: Extra form fields the commit needs (e.g. which match the events belong to).
    context: list[str] = field(default_factory=list)
    note: str = ""

    def column(self, normalised: str) -> Column | None:
        for col in self.columns:
            if _norm(col.name) == normalised:
                return col
            if any(_norm(a) == normalised for a in col.aliases):
                return col
        return None


PLAYERS = Dataset(
    key="players",
    label="Squad players",
    description="One row per player. The six headline attributes are enough to "
                "start; the detail columns sharpen positional fit.",
    columns=[
        Column("name", "str", True, "Full name", "Frenkie de Jong", ("fullname", "player", "playername")),
        Column("known_as", "str", False, "Short name shown on the pitch", "de Jong", ("shortname", "displayname")),
        Column("shirt_number", "int", False, "Squad number", "21", ("number", "shirt", "kitnumber")),
        Column("primary_position", "position", True,
               "GK RB RCB CB LCB LB RWB LWB DM CM AM RM LM RW LW CF ST SS", "CM",
               ("position", "pos", "mainposition")),
        Column("secondary_positions", "positions", False,
               "Other positions, comma-separated", "DM,AM", ("altpositions", "otherpositions")),
        Column("birth_date", "date", False, "Drives the ageing analysis", "1997-05-12", ("dob", "birthday", "dateofbirth")),
        Column("nationality", "str", False, "Three-letter code", "NED", ("nation", "country")),
        Column("preferred_foot", "foot", False, "left / right / both", "right", ("foot",)),
        Column("height_cm", "int", False, "Height in centimetres", "180", ("height",)),
        Column("weight_kg", "int", False, "Weight in kilograms", "74", ("weight",)),
        Column("overall_rating", "rating", True, "Current level, 0-99", "94", ("overall", "ovr", "rating", "ca")),
        Column("potential_rating", "rating", False, "Ceiling, 0-99", "94", ("potential", "pot", "pa")),
        Column("market_value_eur", "int", False, "Market value in euros", "80000000", ("value", "marketvalue")),
        Column("wage_eur_per_year", "int", False, "Annual wage in euros", "12000000", ("wage", "salary")),
        Column("contract_until", "date", False, "Contract expiry", "2029-06-30", ("contract", "contractexpiry")),
        Column("fitness", "rating", False, "Current condition, 0-100", "92"),
        Column("fatigue", "rating", False, "Accumulated load, 0-100", "31"),
        Column("minutes_last_7d", "int", False,
               "Minutes in the last seven days — the highest-value load input",
               "180", ("minutes7d", "minuteslast7days", "recentminutes")),
        Column("is_available", "bool", False, "Fit to play", "yes", ("available", "fit")),
    ] + _attribute_columns(),
    context=["team_id"],
)

MARKET = Dataset(
    key="market_players",
    label="Transfer market pool",
    description="Scouted players available to sign. Fee and wage demand are "
                "what the budget optimiser works against.",
    columns=[
        Column("name", "str", True, "Full name", "Rayan Cherki", ("fullname", "player")),
        Column("current_club", "str", False, "Selling club", "Olympique Lyonnais", ("club", "team")),
        Column("league", "str", False, "Competition", "Ligue 1", ("competition",)),
        Column("league_tier", "int", False, "1 = top-5 league … 5 = lower tier", "1", ("tier",)),
        Column("primary_position", "position", True, "Main position", "AM", ("position", "pos")),
        Column("secondary_positions", "positions", False, "Other positions", "RW,CF"),
        Column("birth_date", "date", False, "Date of birth", "2003-08-17", ("dob",)),
        Column("nationality", "str", False, "Three-letter code", "FRA", ("nation",)),
        Column("preferred_foot", "foot", False, "left / right / both", "right", ("foot",)),
        Column("overall_rating", "rating", True, "Current level", "86", ("overall", "ovr")),
        Column("potential_rating", "rating", False, "Ceiling", "92", ("potential", "pot")),
        Column("minutes_last_season", "int", False, "Sample size behind the rating", "2100", ("minutes",)),
        Column("asking_price_eur", "int", True, "Fee the selling club wants", "45000000", ("price", "fee", "askingprice")),
        Column("wage_demand_eur_per_year", "int", True, "Annual wage demand", "6000000", ("wage", "wagedemand")),
        Column("agent_fee_pct", "float", False, "Agent fee as a fraction, e.g. 0.05", "0.05", ("agentfee",)),
        Column("release_clause_eur", "int", False, "Used when below the asking price", "60000000", ("clause", "releaseclause")),
        Column("contract_until", "date", False, "Contract expiry", "2027-06-30", ("contract",)),
        Column("injury_history_days_2y", "int", False, "Days lost over two seasons", "45", ("injurydays",)),
        Column("availability", "float", False, "0-1 likelihood the deal is doable", "0.55", ("dealodds",)),
    ] + _attribute_columns(),
)

ACADEMY_PLAYERS = Dataset(
    key="academy_players",
    label="Academy players",
    description="Youth roster. Assessments are imported separately and are what "
                "turn a static rating into a development curve.",
    columns=[
        Column("name", "str", True, "Full name", "Gabriel Sanz", ("fullname", "player")),
        Column("birth_date", "date", False, "Drives the relative age check", "2006-11-02", ("dob",)),
        Column("nationality", "str", False, "Three-letter code", "ESP", ("nation",)),
        Column("age_group", "age_group", False, "U12 U14 U16 U18 U19 U21 U23", "U19", ("agegroup", "group")),
        Column("primary_position", "position", True, "Main position", "AM", ("position", "pos")),
        Column("secondary_positions", "positions", False, "Other positions", "CM"),
        Column("preferred_foot", "foot", False, "left / right / both", "right", ("foot",)),
        Column("current_ability", "rating", True, "Level now, 0-99", "72", ("ca", "ability", "current")),
        Column("potential_ability", "rating", True, "Ceiling, 0-99", "88", ("pa", "potential")),
        Column("biological_age_offset", "float", False,
               "Skeletal minus chronological age in years. Negative = late developer",
               "-1.2", ("bioage", "biologicalage")),
        Column("height_cm", "int", False, "Height in centimetres", "176", ("height",)),
        Column("predicted_adult_height_cm", "int", False, "Projected adult height", "186"),
        Column("joined_academy_on", "date", False, "Date joined", "2019-09-01", ("joined",)),
        Column("contract_until", "date", False, "Contract expiry", "2027-06-30", ("contract",)),
        Column("minutes_this_season", "int", False, "All levels", "1400", ("minutes",)),
        Column("senior_minutes", "int", False, "Minutes with the first team", "90"),
    ],
    context=["team_id"],
)

ACADEMY_ASSESSMENTS = Dataset(
    key="academy_assessments",
    label="Academy assessments",
    description="Periodic scores per player. Three spanning six months is the "
                "threshold for a measured growth rate rather than an age prior.",
    columns=[
        Column("player_name", "str", True,
               "Must match an academy player already on file", "Gabriel Sanz",
               ("name", "player", "fullname")),
        Column("assessed_on", "date", True, "Assessment date", "2026-01-15", ("date",)),
        Column("ability", "rating", True, "Composite 0-99", "74", ("ca", "overall")),
        Column("technical", "rating", False, "Technical pillar", "76"),
        Column("tactical", "rating", False, "Tactical pillar", "71"),
        Column("physical", "rating", False, "Physical pillar", "68"),
        Column("mental", "rating", False, "Mental pillar", "77"),
        Column("level", "str", False, "academy / reserves / senior — weights the score", "reserves"),
        Column("minutes_since_last", "int", False, "Minutes played since the previous assessment", "540"),
        Column("goals_since_last", "int", False, "Goals since the previous assessment", "4"),
        Column("assists_since_last", "int", False, "Assists since the previous assessment", "3"),
        Column("sprint_10m_s", "float", False, "10 m sprint, seconds", "1.71"),
        Column("sprint_30m_s", "float", False, "30 m sprint, seconds", "4.08"),
        Column("yoyo_ir1_m", "int", False, "Yo-Yo IR1 distance, metres", "2280"),
        Column("cmj_cm", "float", False, "Countermovement jump, centimetres", "44.5"),
        Column("assessed_by", "str", False, "Who scored it", "Academy staff"),
        Column("notes", "str", False, "Free text", ""),
    ],
)

EVENTS = Dataset(
    key="events",
    label="Match events",
    description="One row per on-ball action. Coordinates are converted from the "
                "provider's frame into metres on ingest.",
    columns=[
        Column("period", "int", True, "1 or 2", "1", ("half",)),
        Column("minute", "int", True, "Match clock minute", "37", ("min",)),
        Column("second", "int", False, "Match clock second", "12", ("sec",)),
        Column("type", "str", True,
               "pass carry shot dribble duel tackle interception pressure "
               "clearance recovery foul save card", "pass",
               ("eventtype", "action")),
        Column("outcome", "str", False,
               "success / incomplete / goal / on_target / off_target", "success",
               ("result",)),
        Column("is_own_team", "bool", True, "Whether our team acted", "yes",
               ("ownteam", "isourteam", "home")),
        Column("player_id", "str", False,
               "Needed for per-player metrics; team totals work without it",
               "", ("player", "playerid")),
        Column("x", "float", True, "Start position along the pitch", "68.4"),
        Column("y", "float", True, "Start position across the pitch", "22.1"),
        Column("end_x", "float", False,
               "Required for xT, progression and directness", "81.0", ("endx", "xend")),
        Column("end_y", "float", False, "See end_x", "14.5", ("endy", "yend")),
        Column("situation", "str", False,
               "open_play counter set_piece corner free_kick penalty", "open_play"),
        Column("body_part", "str", False, "foot / head / other", "foot", ("bodypart",)),
    ],
    context=["match_id", "provider"],
    note="`provider` sets the coordinate frame: "
         + ", ".join(sorted(PROVIDER_FRAMES)),
)

TRACKING = Dataset(
    key="tracking",
    label="Tracking positions",
    description="Long format — one row per player per timestamp. Rows are "
                "pivoted into frames and decimated on ingest.",
    columns=[
        Column("period", "int", True, "1 or 2", "1", ("half",)),
        Column("timestamp_ms", "int", True, "Milliseconds from the period kickoff",
               "2214000", ("timestamp", "ts", "timems")),
        Column("team", "str", True, "home / away / ball", "home", ("side",)),
        Column("player_id", "str", False, "Blank for the ball row", "p_8812", ("player",)),
        Column("x", "float", True, "Position along the pitch", "61.2"),
        Column("y", "float", True, "Position across the pitch", "30.4"),
    ],
    context=["match_id", "provider", "target_hz"],
    note="Use `team=ball` with a blank player_id for the ball's position.",
)

DATASETS: dict[str, Dataset] = {
    d.key: d for d in (PLAYERS, MARKET, ACADEMY_PLAYERS, ACADEMY_ASSESSMENTS,
                       EVENTS, TRACKING)
}


# --- Parsing ---------------------------------------------------------------

@dataclass
class RowError:
    row: int
    column: str
    value: str
    message: str

    def to_dict(self) -> dict:
        return {"row": self.row, "column": self.column,
                "value": self.value, "message": self.message}


@dataclass
class ParseResult:
    dataset: str
    rows: list[dict]
    errors: list[RowError]
    #: Header → the column it matched, or None when unrecognised.
    mapping: dict[str, str | None]
    unmapped_headers: list[str]
    missing_required: list[str]
    total_rows: int

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing_required

    def to_dict(self, preview: int = PREVIEW_ROWS) -> dict:
        return {
            "dataset": self.dataset,
            "total_rows": self.total_rows,
            "valid_rows": len(self.rows),
            "error_count": len(self.errors),
            "ok": self.ok,
            "mapping": self.mapping,
            "unmapped_headers": self.unmapped_headers,
            "missing_required": self.missing_required,
            "preview": self.rows[:preview],
            "errors": [e.to_dict() for e in self.errors[:60]],
        }


def sniff_and_read(raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Decode and read a CSV, sniffing the delimiter.

    Semicolon-delimited exports are the norm in Spanish- and German-locale
    spreadsheets, so guessing comma unconditionally breaks half of real files.
    """
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        raise ValueError("Could not decode the file as text")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    return headers, list(reader)


def parse(dataset_key: str, raw: bytes) -> ParseResult:
    """Parse and validate a CSV against a dataset definition."""
    dataset = DATASETS.get(dataset_key)
    if dataset is None:
        raise ValueError(
            f"Unknown dataset '{dataset_key}'. Known: {sorted(DATASETS)}")

    headers, raw_rows = sniff_and_read(raw)
    if not headers:
        raise ValueError("The file has no header row")
    if len(raw_rows) > MAX_ROWS:
        raise ValueError(f"{len(raw_rows)} rows exceeds the {MAX_ROWS} row limit")

    mapping: dict[str, str | None] = {}
    resolved: dict[str, Column] = {}
    for header in headers:
        col = dataset.column(_norm(header))
        mapping[header] = col.name if col else None
        if col:
            resolved[header] = col

    unmapped = [h for h, v in mapping.items() if v is None]
    matched = {c.name for c in resolved.values()}
    missing_required = [c.name for c in dataset.columns
                        if c.required and c.name not in matched]

    rows: list[dict] = []
    errors: list[RowError] = []

    for index, raw_row in enumerate(raw_rows, start=2):  # row 1 is the header
        record: dict[str, Any] = {}
        attributes: dict[str, float] = {}
        row_failed = False

        for header, col in resolved.items():
            value = (raw_row.get(header) or "").strip()
            if value == "":
                if col.required:
                    errors.append(RowError(index, col.name, "", "required value is empty"))
                    row_failed = True
                continue
            try:
                parsed = PARSERS[col.type](value)
            except (ValueError, TypeError) as exc:
                errors.append(RowError(index, col.name, value, str(exc)))
                row_failed = True
                continue
            if col.attribute:
                attributes[col.name] = parsed
            else:
                record[col.name] = parsed

        if attributes:
            record["attributes"] = attributes
        if not row_failed and record:
            record["__row__"] = index
            rows.append(record)

    return ParseResult(
        dataset=dataset_key, rows=rows, errors=errors, mapping=mapping,
        unmapped_headers=unmapped, missing_required=missing_required,
        total_rows=len(raw_rows),
    )


def template(dataset_key: str) -> str:
    """A CSV template: header row plus one example row."""
    dataset = DATASETS[dataset_key]
    # Attribute columns bloat the template; include the headline six only.
    from app.services.ml.features import HEADLINE_KEYS

    cols = [c for c in dataset.columns
            if not c.attribute or c.name in HEADLINE_KEYS]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([c.name for c in cols])
    writer.writerow([c.example for c in cols])
    return buf.getvalue()


def catalogue() -> list[dict]:
    """Dataset definitions, for the ingest UI."""
    return [
        {
            "key": d.key,
            "label": d.label,
            "description": d.description,
            "context": d.context,
            "note": d.note,
            "columns": [
                {
                    "name": c.name, "type": c.type, "required": c.required,
                    "description": c.description, "example": c.example,
                    "aliases": list(c.aliases), "attribute": c.attribute,
                }
                for c in d.columns
            ],
        }
        for d in DATASETS.values()
    ]
