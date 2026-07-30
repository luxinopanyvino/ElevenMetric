"""Request/response models for the analysis, transfer and academy engines."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.analysis import JobKind, JobStatus
from app.models.catalog import Position


# --- Analysis --------------------------------------------------------------

class MatchAnalysisRequest(BaseModel):
    match_id: str
    lineup_id: str | None = Field(
        default=None, description="Defaults to the match's most recent lineup."
    )
    minute: int = Field(default=90, ge=0, le=130,
                        description="Analyse the match as at this minute.")
    score_difference: int = Field(
        default=0, description="Own goals minus opponent goals. Shifts substitution weighting."
    )
    subs_used: int = Field(default=0, ge=0, le=5)
    windows_used: int = Field(default=0, ge=0, le=3)


class LineupReviewRequest(BaseModel):
    team_id: str
    lineup_id: str | None = None
    formation: str | None = Field(
        default=None, description="Formation to optimise for. Defaults to the team's."
    )
    minute: int = 0
    compare_formations: bool = True


class VideoAnalysisRequest(BaseModel):
    match_id: str | None = None
    team_id: str
    sample_hz: float = Field(default=5.0, gt=0, le=25)
    home_kit_hex: str = "#2a78d6"
    camera_type: str = Field(default="tactical", pattern="^(tactical|broadcast|handheld)$")
    max_seconds: float | None = Field(
        default=None, description="Analyse only the first N seconds; useful for clips."
    )


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    kind: str
    title: str
    detail: str = ""
    priority: float
    confidence: float
    expected_gain: float
    expected_gain_unit: str
    minute_window: str | None = None
    player_out_id: str | None = None
    player_in_id: str | None = None
    drivers: list = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    match_id: str | None = None
    data_completeness: float
    confidence: float
    inputs_used: list
    possession: dict
    heatmaps: dict
    formation: dict
    tactics: dict
    player_metrics: dict
    zones: dict
    phases: dict
    summary: str
    recommendations: list[RecommendationOut] = Field(default_factory=list)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: JobKind
    status: JobStatus
    match_id: str | None = None
    team_id: str | None = None
    progress: float
    stage: str
    engine: str | None = None
    error: str | None = None
    params: dict = Field(default_factory=dict)


# --- Best XI ---------------------------------------------------------------

class BestXIRequest(BaseModel):
    team_id: str
    formation: str = "4-3-3"
    minute: int = 0
    locked: dict[int, str] = Field(
        default_factory=dict,
        description="slot_index -> player_id for players the manager will not drop.",
    )
    bench_size: int = Field(default=7, ge=0, le=12)
    ignore_load: bool = Field(
        default=False,
        description="Rank on raw quality, ignoring accumulated fatigue and fitness. "
                    "False answers 'who should start this weekend'; True answers "
                    "'who is our best XI'.",
    )


# --- Transfers -------------------------------------------------------------

class TransferScanRequest(BaseModel):
    team_id: str
    budget_eur: int | None = Field(
        default=None, description="Defaults to the tenant's transfer budget."
    )
    wage_budget_eur_per_year: int | None = None
    max_signings: int = Field(default=4, ge=1, le=8)
    positions: list[Position] | None = Field(
        default=None,
        description="Restrict the scan to these positions. Defaults to the positions "
                    "the team's formation actually uses.",
    )
    alternative_formations: list[str] = Field(
        default_factory=list,
        description="Shapes the club is willing to move to; their positions are "
                    "included in the scan.",
    )
    max_age: float | None = None
    min_severity: float = Field(
        default=0.18, ge=0.0, le=1.0,
        description="Need severity below which a position is not scanned. Lower it "
                    "to surface marginal upgrades at a strong squad.",
    )
    report_id: str | None = Field(
        default=None,
        description="Feed a match report's vulnerabilities into need detection.",
    )
    shortlist_name: str = "Auto scan"
    #: Tune the objective: quality / fit / value / risk must sum to ~1.
    style_weights: dict[str, float] | None = None


class TransferScanResponse(BaseModel):
    shortlist_id: str
    needs: list[dict]
    targets: list[dict]
    bundle: list[dict]
    budget: dict
    model_version: str = "transfer-recommender-1.0"


# --- Academy ---------------------------------------------------------------

class AcademyReviewRequest(BaseModel):
    team_id: str | None = Field(
        default=None, description="Senior team used to calibrate the first-team bar."
    )
    persist: bool = Field(
        default=True, description="Write projections back onto the academy player rows."
    )


class AcademyReviewResponse(BaseModel):
    projections: list[dict]
    summary: dict


# --- Simulation ------------------------------------------------------------

class SimulationRequest(BaseModel):
    home_team_id: str
    away_team_id: str | None = Field(
        default=None,
        description="Leave empty to face a generated side at `away_strength`.",
    )
    away_name: str = "Opposition"
    away_strength: float = Field(
        default=78.0, ge=40, le=99,
        description="Rating the stand-in opposition is built around.",
    )
    home_formation: str | None = None
    away_formation: str | None = None
    home_press_height: float = Field(default=0.62, ge=0, le=1)
    away_press_height: float = Field(default=0.50, ge=0, le=1)
    minutes: int = Field(default=90, ge=5, le=120)
    seed: int = Field(default=20260728,
                      description="Same seed, same match — the engine is deterministic.")
    playback_hz: float = Field(
        default=0.5, gt=0, le=5,
        description="Positional samples per match-second shipped for playback. "
                    "Higher is smoother and heavier.",
    )
    auto_subs: bool = Field(
        default=True,
        description="Let each side replace spent players at the usual windows.",
    )
    persist: bool = Field(
        default=True,
        description="Store the fixture as a match so it can be analysed afterwards.",
    )


class SimulationResponse(BaseModel):
    match_id: str | None
    opponent_is_synthetic: bool
    summary: dict
    playback: dict
    note: str
