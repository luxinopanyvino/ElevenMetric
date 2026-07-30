from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.academy import AgeGroup, Pathway
from app.models.catalog import Foot, Position
from app.models.match import InputSource, MatchState


# --- Teams -----------------------------------------------------------------

class TeamCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    short_name: str = ""
    country: str | None = None
    competition: str | None = None
    kind: str = "first_team"
    primary_color: str = "#2a78d6"
    secondary_color: str = "#0b0b0b"
    default_formation: str = "4-3-3"


class TeamOut(TeamCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    #: Empty unless an external source supplied this team.
    provenance: dict = Field(default_factory=dict)


# --- Players ---------------------------------------------------------------

class PlayerBase(BaseModel):
    name: str
    known_as: str = ""
    shirt_number: int | None = None
    birth_date: date | None = None
    nationality: str | None = None
    primary_position: Position = Position.CM
    secondary_positions: list[str] = Field(default_factory=list)
    preferred_foot: Foot = Foot.right
    height_cm: int | None = None
    weight_kg: int | None = None
    # Nullable: a source can name a real player and grade none of them. `None`
    # means "nobody has rated this player", which is a different claim from any
    # number we could put here. See app/models/catalog.py.
    overall_rating: float | None = Field(default=70.0, ge=0, le=99)
    potential_rating: float | None = Field(default=75.0, ge=0, le=99)
    attributes: dict = Field(default_factory=dict)
    market_value_eur: int = 0
    wage_eur_per_year: int = 0
    contract_until: date | None = None
    fitness: float = Field(default=100.0, ge=0, le=100)
    fatigue: float = Field(default=0.0, ge=0, le=100)
    injury_risk: float = Field(default=0.05, ge=0, le=1)
    minutes_last_7d: int = 0
    is_available: bool = True

    @field_validator("secondary_positions")
    @classmethod
    def _valid_positions(cls, v: list[str]) -> list[str]:
        valid = {p.value for p in Position}
        bad = [p for p in v if p not in valid]
        if bad:
            raise ValueError(f"Unknown positions: {bad}. Valid: {sorted(valid)}")
        return v

    @field_validator("attributes")
    @classmethod
    def _valid_attributes(cls, v: dict) -> dict:
        """Reject unknown keys and out-of-range values.

        A typo in an attribute name would otherwise sit in the JSON column
        silently and simply never be read, which is far harder to notice than a
        422 at ingest time.
        """
        from app.services.ml.features import ATTRIBUTE_KEYS, headline_from_detail

        known = set(ATTRIBUTE_KEYS)
        if unknown := sorted(set(v) - known):
            raise ValueError(
                f"Unknown attributes: {unknown}. Valid keys: {sorted(known)}"
            )
        for key, value in v.items():
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Attribute '{key}' must be a number, got {value!r}")
            if not 0 <= num <= 99:
                raise ValueError(f"Attribute '{key}' must be 0-99, got {num}")
        # Fill any headline face the caller left out but has detail for.
        return headline_from_detail(v)


class PlayerCreate(PlayerBase):
    team_id: str | None = None


class PlayerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_as: str | None = None
    shirt_number: int | None = None
    team_id: str | None = None
    primary_position: Position | None = None
    secondary_positions: list[str] | None = None
    overall_rating: float | None = Field(default=None, ge=0, le=99)
    potential_rating: float | None = Field(default=None, ge=0, le=99)
    attributes: dict | None = None
    market_value_eur: int | None = None
    wage_eur_per_year: int | None = None
    contract_until: date | None = None
    fitness: float | None = Field(default=None, ge=0, le=100)
    fatigue: float | None = Field(default=None, ge=0, le=100)
    minutes_last_7d: int | None = None
    is_available: bool | None = None


class PlayerOut(PlayerBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    team_id: str | None = None
    display_name: str
    age: float | None = None
    line: str
    #: Empty unless an external source supplied this player.
    provenance: dict = Field(default_factory=dict)


# --- Lineups ---------------------------------------------------------------

class LineupSlotIn(BaseModel):
    player_id: str
    slot_index: int = 0
    is_starter: bool = True
    position: Position = Position.CM
    role: str = "balanced"
    duty: str = "support"
    is_captain: bool = False
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)


class LineupSlotOut(LineupSlotIn):
    model_config = ConfigDict(from_attributes=True)
    id: str
    player_name: str | None = None
    overall_rating: float | None = None


class LineupCreate(BaseModel):
    team_id: str
    match_id: str | None = None
    name: str = "Starting XI"
    formation: str = "4-3-3"
    from_minute: int = 0
    is_template: bool = False
    build_up: str = "balanced"
    defensive_line_height: int = Field(default=50, ge=0, le=100)
    defensive_width: int = Field(default=50, ge=0, le=100)
    pressing_intensity: int = Field(default=50, ge=0, le=100)
    attacking_width: int = Field(default=50, ge=0, le=100)
    tempo: int = Field(default=50, ge=0, le=100)
    counter_press: bool = True
    offside_trap: bool = False
    slots: list[LineupSlotIn] = Field(default_factory=list)


class LineupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    team_id: str
    match_id: str | None = None
    name: str
    formation: str
    from_minute: int
    is_template: bool
    build_up: str
    defensive_line_height: int
    defensive_width: int
    pressing_intensity: int
    attacking_width: int
    tempo: int
    counter_press: bool
    offside_trap: bool
    slots: list[LineupSlotOut] = Field(default_factory=list)


# --- Matches ---------------------------------------------------------------

class MatchCreate(BaseModel):
    team_id: str
    opponent_name: str = "Opponent"
    opponent_team_id: str | None = None
    competition: str = "Friendly"
    season: str = "2025/26"
    kickoff_at: datetime | None = None
    venue: str = "home"
    source: InputSource = InputSource.manual
    provider: str | None = None
    pitch_length_m: float = 105.0
    pitch_width_m: float = 68.0
    notes: str = ""


class MatchOut(MatchCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    state: MatchState
    goals_for: int
    goals_against: int


class EventIn(BaseModel):
    period: int = 1
    minute: int = 0
    second: int = 0
    type: str
    outcome: str = "success"
    is_own_team: bool = True
    player_id: str | None = None
    team_id: str | None = None
    x: float
    y: float
    end_x: float | None = None
    end_y: float | None = None
    qualifiers: dict = Field(default_factory=dict)


class EventBatch(BaseModel):
    #: Coordinate frame the payload is in; converted to metres on ingest.
    provider: str = Field(
        default="elevenmetric",
        description="elevenmetric | statsbomb | opta | wyscout | skillcorner | second_spectrum",
    )
    events: list[EventIn]
    replace_existing: bool = False


class TrackingFrameIn(BaseModel):
    period: int = 1
    timestamp_ms: int
    home_positions: dict[str, list[float]]
    away_positions: dict[str, list[float]] = Field(default_factory=dict)
    ball: list[float] | None = None
    possession_team: str | None = None


class TrackingBatch(BaseModel):
    provider: str = "elevenmetric"
    frames: list[TrackingFrameIn]
    replace_existing: bool = False
    #: Decimate to this rate before storing. 5 Hz is plenty for analytics.
    target_hz: float = 5.0


# --- Academy ---------------------------------------------------------------

class AcademyAssessmentIn(BaseModel):
    assessed_on: date
    assessed_by: str = ""
    ability: float = Field(ge=0, le=99)
    technical: float = Field(default=55, ge=0, le=99)
    tactical: float = Field(default=55, ge=0, le=99)
    physical: float = Field(default=55, ge=0, le=99)
    mental: float = Field(default=55, ge=0, le=99)
    sprint_10m_s: float | None = None
    sprint_30m_s: float | None = None
    yoyo_ir1_m: int | None = None
    cmj_cm: float | None = None
    minutes_since_last: int = 0
    goals_since_last: int = 0
    assists_since_last: int = 0
    level: str = "academy"
    notes: str = ""


class AcademyAssessmentOut(AcademyAssessmentIn):
    model_config = ConfigDict(from_attributes=True)
    id: str


class AcademyPlayerCreate(BaseModel):
    name: str
    team_id: str | None = None
    birth_date: date | None = None
    nationality: str | None = None
    age_group: AgeGroup = AgeGroup.u18
    primary_position: Position = Position.CM
    secondary_positions: list[str] = Field(default_factory=list)
    preferred_foot: Foot = Foot.right
    joined_academy_on: date | None = None
    contract_until: date | None = None
    homegrown: bool = True
    current_ability: float = Field(default=55, ge=0, le=99)
    potential_ability: float = Field(default=72, ge=0, le=99)
    height_cm: int | None = None
    predicted_adult_height_cm: int | None = None
    biological_age_offset: float = 0.0
    minutes_this_season: int = 0
    senior_minutes: int = 0
    notes: str = ""


class AcademyPlayerOut(AcademyPlayerCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    age: float | None = None
    readiness_score: float
    projected_ready_on: date | None = None
    months_to_first_team: float | None = None
    pathway: Pathway
    projection: dict = Field(default_factory=dict)
    assessments: list[AcademyAssessmentOut] = Field(default_factory=list)


# --- Market ----------------------------------------------------------------

class MarketPlayerIn(BaseModel):
    name: str
    current_club: str = ""
    league: str = ""
    league_tier: int = Field(default=3, ge=1, le=5)
    nationality: str | None = None
    birth_date: date | None = None
    primary_position: Position = Position.CM
    secondary_positions: list[str] = Field(default_factory=list)
    preferred_foot: Foot = Foot.right
    overall_rating: float = Field(default=70, ge=0, le=99)
    potential_rating: float = Field(default=75, ge=0, le=99)
    attributes: dict = Field(default_factory=dict)
    per90: dict = Field(default_factory=dict)
    minutes_last_season: int = 0
    asking_price_eur: int = 0
    wage_demand_eur_per_year: int = 0
    agent_fee_pct: float = Field(default=0.05, ge=0, le=0.5)
    contract_until: date | None = None
    release_clause_eur: int | None = None
    injury_history_days_2y: int = 0
    availability: float = Field(default=0.6, ge=0, le=1)
    homegrown: bool = False


class MarketPlayerOut(MarketPlayerIn):
    model_config = ConfigDict(from_attributes=True)
    id: str
    age: float | None = None
    total_cost_eur: int
