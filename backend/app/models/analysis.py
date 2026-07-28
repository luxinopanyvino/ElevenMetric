"""Analysis jobs, their reports, and the recommendations they emit."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TenantScoped, Timestamped, UUIDPk


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobKind(str, enum.Enum):
    lineup_review = "lineup_review"      # static: shape + squad data only
    match_analysis = "match_analysis"    # event and/or tracking data
    video_analysis = "video_analysis"    # CV pipeline then match_analysis
    transfer_scan = "transfer_scan"
    academy_review = "academy_review"


class AnalysisJob(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "analysis_jobs"

    kind: Mapped[JobKind] = mapped_column(Enum(JobKind))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued)

    match_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("matches.id", ondelete="CASCADE"), default=None, index=True
    )
    lineup_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("lineups.id", ondelete="CASCADE"), default=None
    )
    team_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("teams.id", ondelete="CASCADE"), default=None
    )

    #: Everything the caller passed in — replaying a job is a re-POST of this.
    params: Mapped[dict] = mapped_column(JSON, default=dict)

    # --- Video-specific ----------------------------------------------------
    video_path: Mapped[str | None] = mapped_column(String(512), default=None)
    video_duration_s: Mapped[float | None] = mapped_column(Float, default=None)
    #: "yolo+bytetrack" | "simulated" — which engine actually ran.
    engine: Mapped[str | None] = mapped_column(String(64), default=None)

    progress: Mapped[float] = mapped_column(Float, default=0.0)   # 0-1
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    error: Mapped[str | None] = mapped_column(Text, default=None)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    report: Mapped["AnalysisReport | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )


class AnalysisReport(UUIDPk, Timestamped, TenantScoped, Base):
    """The computed output. Large numeric blocks live in JSON columns because
    they are read whole by the UI and never queried field-by-field."""

    __tablename__ = "analysis_reports"

    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("analysis_jobs.id", ondelete="CASCADE"), index=True
    )
    match_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("matches.id", ondelete="CASCADE"), default=None, index=True
    )

    #: How much of the ideal input set was actually present (0-1). Every
    #: recommendation is downweighted by this.
    data_completeness: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    #: Which inputs were used: ["event_data", "tracking", "video", "squad"].
    inputs_used: Mapped[list] = mapped_column(JSON, default=list)

    possession: Mapped[dict] = mapped_column(JSON, default=dict)
    heatmaps: Mapped[dict] = mapped_column(JSON, default=dict)      # {player_id|"team": grid}
    formation: Mapped[dict] = mapped_column(JSON, default=dict)
    tactics: Mapped[dict] = mapped_column(JSON, default=dict)
    player_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    phases: Mapped[dict] = mapped_column(JSON, default=dict)        # in/out of possession splits
    zones: Mapped[dict] = mapped_column(JSON, default=dict)         # 6x5 zone control grid

    summary: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[AnalysisJob] = relationship(back_populates="report")
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class RecommendationKind(str, enum.Enum):
    substitution = "substitution"
    formation_change = "formation_change"
    instruction_change = "instruction_change"
    role_change = "role_change"
    pressing_trigger = "pressing_trigger"
    set_piece = "set_piece"
    transfer = "transfer"
    academy_promotion = "academy_promotion"
    workload = "workload"


class Recommendation(UUIDPk, Timestamped, TenantScoped, Base):
    __tablename__ = "recommendations"

    report_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("analysis_reports.id", ondelete="CASCADE"), index=True, default=None
    )
    kind: Mapped[RecommendationKind] = mapped_column(Enum(RecommendationKind))

    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    #: 0-100. Ranking key in the UI.
    priority: Mapped[float] = mapped_column(Float, default=50.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    #: Estimated effect, in expected-points or xG-difference per 90.
    expected_gain: Mapped[float] = mapped_column(Float, default=0.0)
    expected_gain_unit: Mapped[str] = mapped_column(String(24), default="xGD/90")

    minute_window: Mapped[str | None] = mapped_column(String(24), default=None)  # "60-70"
    player_out_id: Mapped[str | None] = mapped_column(String(32), default=None)
    player_in_id: Mapped[str | None] = mapped_column(String(32), default=None)

    #: The numbers behind the claim, so a coach can audit it.
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Ordered list of human-readable drivers, strongest first.
    drivers: Mapped[list] = mapped_column(JSON, default=list)

    report: Mapped["AnalysisReport | None"] = relationship(back_populates="recommendations")
