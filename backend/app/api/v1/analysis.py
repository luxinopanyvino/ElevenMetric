"""Analysis jobs, reports and video ingestion."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from app.api.deps import CurrentPrincipal, CurrentTenant, Scope, require
from app.core.config import settings
from app.core.tenancy import TenantContext, TenantScope
from app.db.session import SessionLocal
from app.models.analysis import (
    AnalysisJob,
    AnalysisReport,
    JobKind,
    JobStatus,
    Recommendation,
    RecommendationKind,
)
from app.models.catalog import Player, Position, Team
from app.models.match import InputSource, Lineup, Match, MatchEvent, TrackingFrame
from app.models.tenant import Tenant
from app.schemas.ops import (
    JobOut,
    LineupReviewRequest,
    MatchAnalysisRequest,
    RecommendationOut,
    ReportOut,
    VideoAnalysisRequest,
)
from app.services import orchestrator
from app.services.analytics.pitch import Pitch
from app.services.cv import pipeline as cv_pipeline

router = APIRouter(tags=["analysis"])


# --- Assembly helpers ------------------------------------------------------

def _gather(scope: TenantScope, match: Match, lineup: Lineup | None,
            req_minute: int, score_difference: int,
            subs_used: int, windows_used: int) -> orchestrator.AnalysisInput:
    """Load whatever exists for this match into the orchestrator's input."""
    players = {p.id: p for p in scope.all(Player, Player.team_id == match.team_id)}

    starters: list[tuple[Player, Position, int]] = []
    bench: list[Player] = []
    if lineup is not None:
        for slot in lineup.slots:
            player = players.get(slot.player_id)
            if player is None:
                continue
            if slot.is_starter:
                minutes = max(0, req_minute - lineup.from_minute)
                starters.append((player, slot.position, minutes))
            else:
                bench.append(player)

    events = scope.all(
        MatchEvent, MatchEvent.match_id == match.id,
        order_by=(MatchEvent.period, MatchEvent.minute, MatchEvent.second),
    )
    events = [e for e in events if e.minute <= req_minute]

    frames = scope.all(
        TrackingFrame, TrackingFrame.match_id == match.id,
        order_by=(TrackingFrame.period, TrackingFrame.timestamp_ms),
    )
    frames = [f for f in frames if f.timestamp_ms <= req_minute * 60_000]

    return orchestrator.AnalysisInput(
        players=list(players.values()),
        starters=starters,
        bench=bench,
        events=events,
        frames=frames,
        declared_formation=lineup.formation if lineup else None,
        minute=req_minute,
        score_difference=score_difference,
        subs_used=subs_used,
        windows_used=windows_used,
        source=match.source,
        pitch=Pitch(match.pitch_length_m, match.pitch_width_m),
    )


_KIND_MAP = {
    "substitution": RecommendationKind.substitution,
    "formation_change": RecommendationKind.formation_change,
    "instruction_change": RecommendationKind.instruction_change,
    "role_change": RecommendationKind.role_change,
    "workload": RecommendationKind.workload,
    "transfer": RecommendationKind.transfer,
    "academy_promotion": RecommendationKind.academy_promotion,
}


def _persist(scope: TenantScope, job: AnalysisJob,
             out: orchestrator.AnalysisOutput) -> AnalysisReport:
    report = AnalysisReport(
        job_id=job.id,
        match_id=job.match_id,
        data_completeness=out.data_completeness,
        confidence=out.confidence,
        inputs_used=out.inputs_used,
        possession=out.possession,
        heatmaps=out.heatmaps,
        formation=out.formation,
        tactics=out.tactics,
        player_metrics=out.player_metrics,
        zones=out.zones,
        phases=out.phases,
        summary=out.summary,
    )
    scope.add(report)
    scope.flush()

    for r in out.recommendations:
        scope.add(Recommendation(
            report_id=report.id,
            kind=_KIND_MAP.get(r["kind"], RecommendationKind.instruction_change),
            title=r["title"], detail=r.get("detail", ""),
            priority=r["priority"], confidence=r["confidence"],
            expected_gain=r.get("expected_gain", 0.0),
            expected_gain_unit=r.get("expected_gain_unit", "xGD/90"),
            minute_window=r.get("minute_window"),
            player_out_id=r.get("player_out_id"), player_in_id=r.get("player_in_id"),
            evidence=r.get("evidence", {}), drivers=r.get("drivers", []),
        ))

    job.status = JobStatus.succeeded
    job.progress = 1.0
    job.stage = "done"
    job.finished_at = datetime.now(timezone.utc)
    scope.commit()
    scope.refresh(report)
    return report


def _report_out(report: AnalysisReport, scope: TenantScope) -> ReportOut:
    recs = scope.all(Recommendation, Recommendation.report_id == report.id,
                     order_by=Recommendation.priority.desc())
    return ReportOut(
        id=report.id, job_id=report.job_id, match_id=report.match_id,
        data_completeness=report.data_completeness, confidence=report.confidence,
        inputs_used=report.inputs_used, possession=report.possession,
        heatmaps=report.heatmaps, formation=report.formation, tactics=report.tactics,
        player_metrics=report.player_metrics, zones=report.zones, phases=report.phases,
        summary=report.summary,
        recommendations=[
            RecommendationOut(
                id=r.id, kind=r.kind.value, title=r.title, detail=r.detail,
                priority=r.priority, confidence=r.confidence,
                expected_gain=r.expected_gain, expected_gain_unit=r.expected_gain_unit,
                minute_window=r.minute_window, player_out_id=r.player_out_id,
                player_in_id=r.player_in_id, drivers=r.drivers, evidence=r.evidence,
            )
            for r in recs
        ],
    )


# --- Synchronous analyses --------------------------------------------------

@router.post("/analysis/match", response_model=ReportOut,
             dependencies=[Depends(require("analysis:run"))])
def analyse_match(payload: MatchAnalysisRequest, scope: Scope) -> ReportOut:
    """Run the full analysis over a match's stored data.

    Synchronous: with data already ingested this is fast (a 90-minute event feed
    analyses in well under a second). Video is the asynchronous path.
    """
    match = scope.get(Match, payload.match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    lineup = None
    if payload.lineup_id:
        lineup = scope.get(Lineup, payload.lineup_id)
        if lineup is None:
            raise HTTPException(status_code=404, detail="Lineup not found")
    else:
        lineups = scope.all(Lineup, Lineup.match_id == match.id,
                            order_by=Lineup.from_minute.desc())
        lineup = lineups[0] if lineups else None

    job = AnalysisJob(
        kind=JobKind.match_analysis, status=JobStatus.running,
        match_id=match.id, lineup_id=lineup.id if lineup else None,
        team_id=match.team_id, params=payload.model_dump(),
        started_at=datetime.now(timezone.utc), stage="analysing",
    )
    scope.add(job)
    scope.flush()

    inp = _gather(scope, match, lineup, payload.minute, payload.score_difference,
                  payload.subs_used, payload.windows_used)
    if not (inp.events or inp.frames or inp.starters):
        job.status = JobStatus.failed
        job.error = "No data for this match: ingest events, tracking or a lineup first."
        scope.commit()
        raise HTTPException(status_code=422, detail=job.error)

    out = orchestrator.analyse(inp)
    report = _persist(scope, job, out)
    return _report_out(report, scope)


@router.post("/analysis/lineup", response_model=ReportOut,
             dependencies=[Depends(require("analysis:run"))])
def review_lineup(payload: LineupReviewRequest, scope: Scope) -> ReportOut:
    """Review a shape with no match data — the pre-match planning path."""
    team = scope.get(Team, payload.team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    lineup = scope.get(Lineup, payload.lineup_id) if payload.lineup_id else None
    players = scope.all(Player, Player.team_id == team.id)
    if not players:
        raise HTTPException(status_code=422, detail="This team has no players yet")

    starters: list[tuple[Player, Position, int]] = []
    bench: list[Player] = []
    by_id = {p.id: p for p in players}
    if lineup is not None:
        for slot in lineup.slots:
            p = by_id.get(slot.player_id)
            if p is None:
                continue
            if slot.is_starter:
                starters.append((p, slot.position, payload.minute))
            else:
                bench.append(p)

    job = AnalysisJob(
        kind=JobKind.lineup_review, status=JobStatus.running, team_id=team.id,
        lineup_id=lineup.id if lineup else None, params=payload.model_dump(),
        started_at=datetime.now(timezone.utc), stage="analysing",
    )
    scope.add(job)
    scope.flush()

    inp = orchestrator.AnalysisInput(
        players=players, starters=starters, bench=bench,
        declared_formation=payload.formation
        or (lineup.formation if lineup else team.default_formation),
        minute=payload.minute, source=InputSource.manual,
    )
    out = orchestrator.analyse(inp)
    report = _persist(scope, job, out)
    return _report_out(report, scope)


# --- Video (asynchronous) --------------------------------------------------

def _run_video_job(job_id: str, ctx_dict: dict, params: dict) -> None:
    """Background worker. Owns its own session — the request's is long gone."""
    ctx = TenantContext(**ctx_dict)
    db = SessionLocal()
    scope = TenantScope(db, ctx)
    try:
        job = scope.get(AnalysisJob, job_id)
        if job is None:
            return
        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc)
        job.stage = "decoding"
        scope.commit()

        def progress(pct: float, stage: str) -> None:
            job.progress = round(pct, 3)
            job.stage = stage
            scope.commit()

        result = cv_pipeline.run(
            job.video_path,
            sample_hz=params.get("sample_hz", 5.0),
            home_kit_hex=params.get("home_kit_hex", "#2a78d6"),
            progress=progress,
            max_seconds=params.get("max_seconds"),
        )

        job.engine = result.engine
        job.video_duration_s = result.duration_s
        job.stage = "persisting tracking"
        scope.commit()

        match = scope.get(Match, job.match_id) if job.match_id else None
        if match is None:
            match = Match(
                team_id=job.team_id or "", opponent_name="Video upload",
                competition="Video analysis", source=InputSource.video,
                provider=result.engine,
            )
            scope.add(match)
            scope.flush()
            job.match_id = match.id

        scope.delete_where(TrackingFrame, TrackingFrame.match_id == match.id)
        scope.add_all([
            TrackingFrame(
                match_id=match.id, period=f.period, timestamp_ms=f.timestamp_ms,
                home_positions=f.home_positions, away_positions=f.away_positions,
                ball=f.ball, possession_team=f.possession_team,
            )
            for f in result.frames
        ])
        match.source = InputSource.video
        scope.commit()

        job.stage = "analysing"
        scope.commit()

        lineups = scope.all(Lineup, Lineup.match_id == match.id,
                            order_by=Lineup.from_minute.desc())
        inp = _gather(scope, match, lineups[0] if lineups else None, 90, 0, 0, 0)
        inp.cv_meta = result.to_dict()
        out = orchestrator.analyse(inp)
        out.warnings.extend(result.warnings)
        if result.warnings:
            out.summary = " ".join(result.warnings) + " " + out.summary

        _persist(scope, job, out)

        # Bill the tenant for the footage that was actually processed.
        tenant = scope.db.get(Tenant, ctx.tenant_id)
        if tenant is not None and result.duration_s:
            tenant.video_minutes_used += max(1, int(result.duration_s / 60))
            scope.commit()
    except Exception as exc:  # pragma: no cover - defensive
        db.rollback()
        job = scope.get(AnalysisJob, job_id)
        if job is not None:
            job.status = JobStatus.failed
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = datetime.now(timezone.utc)
            scope.commit()
    finally:
        db.close()


@router.post("/video/analyze", response_model=JobOut, status_code=202,
             dependencies=[Depends(require("video:upload"))])
async def analyse_video(
    background: BackgroundTasks,
    scope: Scope,
    ctx: CurrentPrincipal,
    tenant: CurrentTenant,
    file: Annotated[UploadFile, File(description="MP4/MOV/MKV footage")],
    team_id: Annotated[str, Form()],
    match_id: Annotated[str | None, Form()] = None,
    sample_hz: Annotated[float, Form()] = 5.0,
    home_kit_hex: Annotated[str, Form()] = "#2a78d6",
    camera_type: Annotated[str, Form()] = "tactical",
    max_seconds: Annotated[float | None, Form()] = None,
) -> JobOut:
    """Upload footage and queue a CV analysis.

    Returns immediately with a job id; poll ``GET /analysis/jobs/{id}``.
    """
    if scope.get(Team, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    if match_id and scope.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")

    suffix = Path(file.filename or "upload.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".avi", ".m4v"}:
        raise HTTPException(status_code=415, detail=f"Unsupported video format '{suffix}'")

    limit = tenant.limits["video_minutes_per_month"]
    if tenant.video_minutes_used >= limit:
        raise HTTPException(
            status_code=402,
            detail=f"Plan '{tenant.plan.value}' allows {limit} video minutes per month; "
                   f"{tenant.video_minutes_used} used.",
        )

    dest_dir = Path(settings.media_root) / ctx.tenant_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{suffix}"

    size = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds the {settings.max_upload_mb} MB limit",
                )
            fh.write(chunk)

    params = VideoAnalysisRequest(
        match_id=match_id, team_id=team_id, sample_hz=sample_hz,
        home_kit_hex=home_kit_hex, camera_type=camera_type, max_seconds=max_seconds,
    ).model_dump()

    job = AnalysisJob(
        kind=JobKind.video_analysis, status=JobStatus.queued, team_id=team_id,
        match_id=match_id, params=params, video_path=str(dest), stage="queued",
        engine=cv_pipeline.capabilities()["engine"],
    )
    scope.add(job)
    scope.commit()
    scope.refresh(job)

    background.add_task(_run_video_job, job.id, asdict(ctx), params)
    return JobOut.model_validate(job)


@router.get("/video/capabilities")
def video_capabilities() -> dict:
    """What the CV pipeline can actually do on this deployment."""
    return cv_pipeline.capabilities()


# --- Jobs and reports ------------------------------------------------------

@router.get("/analysis/jobs", response_model=list[JobOut])
def list_jobs(scope: Scope, match_id: str | None = None, limit: int = 50) -> list[JobOut]:
    criteria = [AnalysisJob.match_id == match_id] if match_id else []
    jobs = scope.all(AnalysisJob, *criteria, limit=limit,
                     order_by=AnalysisJob.created_at.desc())
    return [JobOut.model_validate(j) for j in jobs]


@router.get("/analysis/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, scope: Scope) -> JobOut:
    job = scope.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut.model_validate(job)


@router.get("/analysis/jobs/{job_id}/report", response_model=ReportOut)
def get_job_report(job_id: str, scope: Scope) -> ReportOut:
    job = scope.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    report = scope.first(AnalysisReport, AnalysisReport.job_id == job_id)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"No report yet — job status is '{job.status.value}' ({job.stage})",
        )
    return _report_out(report, scope)


@router.get("/analysis/reports", response_model=list[ReportOut])
def list_reports(scope: Scope, match_id: str | None = None, limit: int = 20) -> list[ReportOut]:
    criteria = [AnalysisReport.match_id == match_id] if match_id else []
    reports = scope.all(AnalysisReport, *criteria, limit=limit,
                        order_by=AnalysisReport.created_at.desc())
    return [_report_out(r, scope) for r in reports]


@router.get("/analysis/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: str, scope: Scope) -> ReportOut:
    report = scope.get(AnalysisReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return _report_out(report, scope)
