"""Metadata: the input contract, model registry and reference data."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Scope
from app.models.academy import AcademyAssessment, AcademyPlayer, AgeGroup, Pathway
from app.models.catalog import (
    POSITION_ANCHOR,
    POSITION_LINE,
    Foot,
    Player,
    Position,
    Team,
)
from app.models.match import InputSource, Lineup, Match, MatchEvent, TrackingFrame
from app.models.tenant import PLAN_LIMITS, ROLE_CAPABILITIES, Plan, Role
from app.services import data_requirements
from app.services.analytics.pitch import (
    PROVIDER_FRAMES,
    ZONE_COLS,
    ZONE_ROWS,
    zone_labels,
)
from app.services.cv import pipeline as cv_pipeline
from app.services.ml.features import POSITION_WEIGHTS
from app.services.ml.lineup_optimizer import FORMATION_SLOTS
from app.services.ml.registry import model_catalogue

router = APIRouter(tags=["meta"])


@router.get("/meta/data-requirements")
def data_contract() -> dict:
    """**What data does ElevenMetric need?**

    The full input contract, tier by tier: which fields, which are required,
    what each tier unlocks, and what remains impossible without the next one.
    """
    return data_requirements.catalogue()


@router.get("/meta/data-readiness")
def data_readiness(scope: Scope) -> dict:
    """Score this club's actual data against the contract."""
    return data_requirements.assess(
        has_players=scope.count(Player) > 0,
        has_lineups=scope.count(Lineup) > 0,
        has_events=scope.count(MatchEvent) > 0,
        has_tracking=scope.count(TrackingFrame) > 0,
        has_video=scope.count(Match, Match.source == InputSource.video) > 0,
        has_market=_has_market(scope),
        has_budget=_has_budget(scope),
        has_academy_assessments=scope.count(AcademyAssessment) > 0,
    )


def _has_market(scope: Scope) -> bool:
    from app.models.transfer import MarketPlayer

    return scope.count(MarketPlayer) > 0


def _has_budget(scope: Scope) -> bool:
    from app.models.tenant import Tenant

    tenant = scope.db.get(Tenant, scope.ctx.tenant_id)
    return bool(tenant and tenant.transfer_budget_eur > 0)


@router.get("/meta/reference")
def reference() -> dict:
    """Enumerations and geometry the frontend renders against."""
    return {
        "positions": [
            {
                "value": p.value,
                "line": POSITION_LINE[p],
                "anchor_m": list(POSITION_ANCHOR[p]),
                "anchor_norm": [
                    round(POSITION_ANCHOR[p][0] / 105.0, 4),
                    round(POSITION_ANCHOR[p][1] / 68.0, 4),
                ],
            }
            for p in Position
        ],
        "position_weights": POSITION_WEIGHTS,
        "formations": {k: [p.value for p in v] for k, v in FORMATION_SLOTS.items()},
        "feet": [f.value for f in Foot],
        "age_groups": [a.value for a in AgeGroup],
        "pathways": [p.value for p in Pathway],
        "roles": {r.value: sorted(ROLE_CAPABILITIES[r]) for r in Role},
        "plans": {p.value: PLAN_LIMITS[p] for p in Plan},
        "input_sources": [s.value for s in InputSource],
        "provider_frames": {
            k: {"x_max": v[0], "y_max": v[1], "y_flipped": v[2]}
            for k, v in PROVIDER_FRAMES.items()
        },
        "pitch": {"length_m": 105.0, "width_m": 68.0},
        "zone_grid": {"cols": ZONE_COLS, "rows": ZONE_ROWS, "labels": zone_labels()},
    }


@router.get("/meta/models")
def models() -> dict:
    """Model registry: versions, features and holdout metrics.

    ``provenance: "bootstrap"`` means the model was fitted on a documented
    generative process rather than on real matches — calibrated and monotonic,
    but a prior to be replaced with club data.
    """
    return {
        "models": model_catalogue(),
        "analytics": {
            "xg": {"version": "xg-logistic-1.0",
                   "features": ["distance_m", "visible_angle_rad", "situation",
                                "body_part", "defenders_in_cone (optional)"]},
            "xt": {"version": "xt-grid-1.0",
                   "note": "16x12 grid from value iteration over a move/shoot MDP"},
        },
        "cv": cv_pipeline.capabilities(),
    }


@router.get("/meta/overview")
def overview(scope: Scope) -> dict:
    """Counts across the tenant — what the dashboard header reads."""
    return {
        "teams": scope.count(Team),
        "players": scope.count(Player),
        "academy_players": scope.count(AcademyPlayer),
        "matches": scope.count(Match),
        "events": scope.count(MatchEvent),
        "tracking_frames": scope.count(TrackingFrame),
        "lineups": scope.count(Lineup),
    }
