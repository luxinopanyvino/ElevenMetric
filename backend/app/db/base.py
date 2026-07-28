"""Import surface that registers every model on ``Base.metadata``."""

from app.db.base_class import Base  # noqa: F401
from app.models.academy import AcademyAssessment, AcademyPlayer  # noqa: F401
from app.models.analysis import AnalysisJob, AnalysisReport, Recommendation  # noqa: F401
from app.models.catalog import Player, PlayerSeasonStat, Team  # noqa: F401
from app.models.match import (  # noqa: F401
    Lineup,
    LineupSlot,
    Match,
    MatchEvent,
    TrackingFrame,
)
from app.models.tenant import ApiKey, Tenant, User  # noqa: F401
from app.models.transfer import MarketPlayer, TransferShortlist, TransferTarget  # noqa: F401
