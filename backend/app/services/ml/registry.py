"""Model registry.

Each engine ships a **bootstrap prior**: a small scikit-learn model fitted on
data drawn from an explicit generative process rather than on real matches. It
gives calibrated, monotonic behaviour on day one, and is designed to be
replaced — call :func:`fit_from_dataset` with a club's own history and the
refitted model is persisted and used instead.

Every model exposes ``version``; reports record it so numbers stay comparable.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings

MODEL_DIR = Path(settings.media_root).parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

_RNG_SEED = 20260728


@dataclass
class ModelBundle:
    name: str
    version: str
    estimator: Any
    feature_names: list[str]
    #: "bootstrap" until refitted on real observations.
    provenance: str = "bootstrap"
    metrics: dict | None = None

    def predict(self, rows: list[dict]) -> np.ndarray:
        X = np.array([[r.get(f, 0.0) for f in self.feature_names] for r in rows], dtype=float)
        return self.estimator.predict(X)

    def save(self) -> Path:
        path = MODEL_DIR / f"{self.name}.pkl"
        with path.open("wb") as fh:
            pickle.dump(
                {
                    "version": self.version,
                    "estimator": self.estimator,
                    "feature_names": self.feature_names,
                    "provenance": self.provenance,
                    "metrics": self.metrics,
                },
                fh,
            )
        (MODEL_DIR / f"{self.name}.json").write_text(
            json.dumps(
                {
                    "name": self.name,
                    "version": self.version,
                    "provenance": self.provenance,
                    "features": self.feature_names,
                    "metrics": self.metrics,
                },
                indent=2,
            )
        )
        return path

    @classmethod
    def load(cls, name: str) -> "ModelBundle | None":
        path = MODEL_DIR / f"{name}.pkl"
        if not path.exists():
            return None
        try:
            with path.open("rb") as fh:
                blob = pickle.load(fh)
        except Exception:
            return None
        return cls(
            name=name,
            version=blob["version"],
            estimator=blob["estimator"],
            feature_names=blob["feature_names"],
            provenance=blob.get("provenance", "unknown"),
            metrics=blob.get("metrics"),
        )


# --- Impact model ----------------------------------------------------------

IMPACT_FEATURES = [
    "effective_level",
    "position_fit",
    "performance_multiplier",
    "minutes_remaining",
    "fresh_legs_edge",
    "tactical_need",
    "score_state",
]

IMPACT_VERSION = "impact-ridge-1.0"


def _synthesise_impact_dataset(n: int = 6000) -> tuple[np.ndarray, np.ndarray]:
    """Generative process behind the bootstrap impact prior.

    A player's contribution to expected goal difference over the remainder of a
    match is modelled as roughly linear in their effective level and positional
    fit, scaled by the minutes left, with a genuine but modest bonus for fresh
    legs against tired opponents, and a tactical-need term that captures a
    change addressing a specific weakness.
    """
    rng = np.random.default_rng(_RNG_SEED)

    effective_level = rng.normal(72, 8, n).clip(45, 95)
    position_fit = rng.beta(6, 2, n)
    perf_mult = rng.uniform(0.62, 1.0, n)
    minutes_remaining = rng.integers(1, 91, n).astype(float)
    fresh_edge = rng.uniform(0, 1, n)
    tactical_need = rng.uniform(0, 1, n)
    score_state = rng.integers(-2, 3, n).astype(float)

    minutes_scale = minutes_remaining / 90.0
    y = (
        0.0135 * (effective_level - 70.0)
        + 0.28 * (position_fit - 0.8)
        + 0.22 * (perf_mult - 0.85)
        + 0.16 * fresh_edge
        + 0.20 * tactical_need
        - 0.020 * score_state
    ) * minutes_scale
    y += rng.normal(0, 0.035, n)

    X = np.column_stack([
        effective_level, position_fit, perf_mult, minutes_remaining,
        fresh_edge, tactical_need, score_state,
    ])
    return X, y


def _fit_impact() -> ModelBundle:
    from sklearn.linear_model import Ridge
    from sklearn.metrics import mean_absolute_error, r2_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import PolynomialFeatures, StandardScaler

    X, y = _synthesise_impact_dataset()
    split = int(0.8 * len(X))
    est = make_pipeline(
        PolynomialFeatures(degree=2, include_bias=False),
        StandardScaler(),
        Ridge(alpha=1.0),
    )
    est.fit(X[:split], y[:split])
    pred = est.predict(X[split:])
    metrics = {
        "r2_holdout": round(float(r2_score(y[split:], pred)), 4),
        "mae_holdout": round(float(mean_absolute_error(y[split:], pred)), 4),
        "n_train": split,
        "target": "xGD contribution over remaining minutes",
    }
    bundle = ModelBundle(
        name="impact", version=IMPACT_VERSION, estimator=est,
        feature_names=IMPACT_FEATURES, provenance="bootstrap", metrics=metrics,
    )
    bundle.save()
    return bundle


# --- Academy growth model --------------------------------------------------

ACADEMY_FEATURES = [
    "current_ability", "potential_ability", "age", "growth_rate_per_year",
    "biological_age_offset", "minutes_ratio", "technical", "tactical",
    "physical", "mental",
]

ACADEMY_VERSION = "academy-gbr-1.0"


def _synthesise_academy_dataset(n: int = 5000) -> tuple[np.ndarray, np.ndarray]:
    """Target: months until the player reaches first-team level.

    Development is modelled as logistic approach to the ceiling, faster for
    younger players and for late developers once they catch up physically, and
    materially faster when the player is already getting minutes at a level
    above their age group.
    """
    rng = np.random.default_rng(_RNG_SEED + 1)

    age = rng.uniform(15, 22, n)
    potential = rng.normal(74, 7, n).clip(55, 95)
    gap_frac = rng.uniform(0.05, 0.85, n)
    current = potential - gap_frac * (potential - 45)
    growth = rng.uniform(1.0, 7.5, n)
    bio_offset = rng.normal(0, 0.9, n)
    minutes_ratio = rng.beta(2, 4, n)
    technical = current + rng.normal(0, 4, n)
    tactical = current + rng.normal(0, 5, n)
    physical = current + rng.normal(0, 6, n) + bio_offset * 4
    mental = current + rng.normal(0, 5, n)

    target_level = 68.0
    remaining = np.clip(target_level - current, 0, None)
    # Effective growth: base rate, damped as the player nears their ceiling,
    # boosted by senior minutes and by a late developer's pending maturation.
    headroom = np.clip((potential - current) / np.maximum(potential - 45, 1), 0.05, 1.0)
    effective = growth * headroom * (1 + 0.55 * minutes_ratio) * (1 - 0.045 * np.clip(age - 19, 0, None))
    effective += np.clip(-bio_offset, 0, None) * 1.1
    effective = np.clip(effective, 0.25, None)

    months = 12.0 * remaining / effective
    months = np.clip(months + rng.normal(0, 2.0, n), 0, 96)
    # A ceiling below first-team level means "never" — capped at the horizon.
    months = np.where(potential < target_level + 1.5, 96.0, months)

    X = np.column_stack([
        current, potential, age, growth, bio_offset, minutes_ratio,
        technical, tactical, physical, mental,
    ])
    return X, months


def _fit_academy() -> ModelBundle:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error, r2_score

    X, y = _synthesise_academy_dataset()
    split = int(0.8 * len(X))
    est = GradientBoostingRegressor(
        n_estimators=220, max_depth=3, learning_rate=0.06,
        subsample=0.9, random_state=_RNG_SEED,
    )
    est.fit(X[:split], y[:split])
    pred = est.predict(X[split:])
    metrics = {
        "r2_holdout": round(float(r2_score(y[split:], pred)), 4),
        "mae_months_holdout": round(float(mean_absolute_error(y[split:], pred)), 3),
        "n_train": split,
        "target": "months until first-team level (68 CA)",
    }
    bundle = ModelBundle(
        name="academy", version=ACADEMY_VERSION, estimator=est,
        feature_names=ACADEMY_FEATURES, provenance="bootstrap", metrics=metrics,
    )
    bundle.save()
    return bundle


# --- Public accessors ------------------------------------------------------

_CACHE: dict[str, ModelBundle] = {}

_FITTERS = {"impact": _fit_impact, "academy": _fit_academy}


def get_model(name: str) -> ModelBundle:
    if name in _CACHE:
        return _CACHE[name]
    bundle = ModelBundle.load(name)
    expected = {"impact": IMPACT_VERSION, "academy": ACADEMY_VERSION}.get(name)
    if bundle is None or (expected and bundle.version != expected):
        if name not in _FITTERS:
            raise KeyError(f"Unknown model '{name}'")
        bundle = _FITTERS[name]()
    _CACHE[name] = bundle
    return bundle


def fit_from_dataset(
    name: str, rows: list[dict], target: list[float], *, version: str | None = None
) -> ModelBundle:
    """Refit a model on real observations, replacing the bootstrap prior."""
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error

    features = {"impact": IMPACT_FEATURES, "academy": ACADEMY_FEATURES}[name]
    X = np.array([[r.get(f, 0.0) for f in features] for r in rows], dtype=float)
    y = np.asarray(target, dtype=float)
    if len(X) < 50:
        raise ValueError("need at least 50 observations to refit; keeping the bootstrap prior")

    split = max(1, int(0.8 * len(X)))
    est = GradientBoostingRegressor(random_state=_RNG_SEED)
    est.fit(X[:split], y[:split])
    metrics = {
        "mae_holdout": round(float(mean_absolute_error(y[split:], est.predict(X[split:]))), 4)
        if split < len(X) else None,
        "n_train": split,
    }
    bundle = ModelBundle(
        name=name,
        version=version or f"{name}-club-fitted",
        estimator=est,
        feature_names=features,
        provenance="club_data",
        metrics=metrics,
    )
    bundle.save()
    _CACHE[name] = bundle
    return bundle


def model_catalogue() -> list[dict]:
    out = []
    for name in _FITTERS:
        b = get_model(name)
        out.append({
            "name": b.name, "version": b.version, "provenance": b.provenance,
            "features": b.feature_names, "metrics": b.metrics,
        })
    return out
