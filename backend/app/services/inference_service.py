"""Load approved offline artifacts and score one explicitly supplied feature row."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from fastapi import HTTPException
from math import isfinite

from app.dataset_generator import LABEL_COLUMNS
from app.model_evaluation import FEATURE_ALLOWLIST

ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "data" / "model_evaluation" / "experiments" / "validation_selection_20261014" / "models"
MODEL_ID = "synthetic-validation-selected-decision-tree-v1"
ARTIFACT_VERSION = "routine-baselines-v1"
DECISION_THRESHOLDS = {
    "label_unusual_wake_within_60m": 0.05,
    "label_delayed_medication_access_within_30m": 0.5,
    "label_room_temp_out_of_range_within_30m": 0.5,
}


@lru_cache(maxsize=len(LABEL_COLUMNS))
def _load_model(target: str) -> Any:
    if target not in LABEL_COLUMNS:
        raise HTTPException(status_code=422, detail="Unsupported prediction target.")
    path = ARTIFACT_DIR / f"{target}.joblib"
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"Model artifact is unavailable for {target}.")
    try:
        model = joblib.load(path)
        if isinstance(model, dict):
            if (model.get("artifact_version") != ARTIFACT_VERSION
                    or model.get("target") != target
                    or model.get("feature_names") != list(FEATURE_ALLOWLIST)
                    or "estimator" not in model):
                raise ValueError("artifact metadata does not match the inference contract")
            model = model["estimator"]
        if getattr(model, "n_features_in_", None) != len(FEATURE_ALLOWLIST):
            raise ValueError("feature count does not match the approved feature contract")
        return model
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Model artifact is incompatible for {target}.") from exc


def predict(target: str, features: dict[str, float | None]) -> dict[str, Any]:
    unknown = sorted(set(features) - set(FEATURE_ALLOWLIST))
    missing = sorted(set(FEATURE_ALLOWLIST) - set(features))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unsupported features: {', '.join(unknown)}")
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required features: {', '.join(missing)}")
    bounds = {
        "feature_hour_sin": (-1, 1), "feature_hour_cos": (-1, 1),
        "feature_expected_wake_hour_local": (0, 24),
        "feature_minutes_from_expected_wake": (-1440, 1440),
        "feature_motion_intensity_m_s2": (0, 100),
        "feature_recent_motion_mean_30m_m_s2": (0, 100),
        "feature_minutes_since_pillbox_open": (0, 10080),
    }
    for name, value in features.items():
        if value is not None and (not isfinite(value) or not bounds[name][0] <= value <= bounds[name][1]):
            raise HTTPException(status_code=422, detail=f"{name} is outside the supported numeric range.")
    values = [features[name] for name in FEATURE_ALLOWLIST]
    if not any(value is not None for value in values):
        raise HTTPException(status_code=422, detail="At least one feature observation is required.")
    if any(value is None for value in values):
        missing_names = [name for name in FEATURE_ALLOWLIST if features[name] is None]
        raise HTTPException(status_code=422, detail=f"Incomplete feature row; unavailable features: {', '.join(missing_names)}")
    model = _load_model(target)
    row = np.asarray([values], dtype=float)
    try:
        probability = float(model.predict_proba(row)[0, 1])
        threshold = DECISION_THRESHOLDS[target]
        prediction = int(probability >= threshold)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Model inference failed for the supplied feature row.") from exc
    return {
        "target": target,
        "prediction": prediction,
        "probability": probability,
        "decision_threshold": DECISION_THRESHOLDS[target],
        "model_id": MODEL_ID,
        "model_version": MODEL_ID,
        "data_quality": "complete",
        "data_freshness_minutes": None,
        "features_used": list(FEATURE_ALLOWLIST),
        "missing_feature_count": 0,
        "synthetic_only": True,
        "reminder_eligible": False,
    }
