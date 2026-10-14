"""Conservative reminder eligibility and in-process target cooldowns."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from app.dataset_generator import LABEL_COLUMNS

REMINDER_ENABLED = {target: False for target in LABEL_COLUMNS}
DEMO_TARGET = "demo_schedule_checkin"
REMINDER_ENABLED[DEMO_TARGET] = True
COOLDOWN = timedelta(minutes=30)


class ReminderPolicy:
    def __init__(self, *, enabled: dict[str, bool] | None = None, cooldown: timedelta = COOLDOWN) -> None:
        self.enabled = dict(REMINDER_ENABLED if enabled is None else enabled)
        self.cooldown = cooldown
        self._last_triggered: dict[str, datetime] = {}
        self._lock = Lock()

    def decide(self, prediction: dict[str, Any], *, observed_at: datetime, now: datetime | None = None) -> dict[str, Any]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        observed = observed_at.astimezone(timezone.utc)
        target = prediction.get("target")
        reason = ""
        eligible = False
        is_demo_rule = target == DEMO_TARGET
        if target not in LABEL_COLUMNS and not is_demo_rule:
            reason = "Unsupported target."
        elif observed > current or current - observed > timedelta(minutes=10):
            reason = "Observation is stale or timestamp is in the future."
        elif prediction.get("missing_feature_count", 1) != 0:
            reason = "Required feature observations are incomplete."
        elif is_demo_rule and (
            prediction.get("source") != "simulated_demo"
            or prediction.get("decision_kind") != "rule_based"
            or prediction.get("scenario_id") != "scheduled_check_in"
        ):
            reason = "The demo reminder requires the explicit scheduled-check-in rule scenario."
        elif not self.enabled.get(target, False):
            reason = "Autonomous reminders are disabled for this synthetic-only target."
        elif prediction.get("prediction") != 1:
            reason = "No positive event prediction."
        else:
            with self._lock:
                previous = self._last_triggered.get(target)
                if previous is not None and current - previous < self.cooldown:
                    reason = "Suppressed by the per-target cooldown."
                else:
                    self._last_triggered[target] = current
                    eligible = True
                    reason = (
                        "Explicit simulated scheduled-check-in rule passed reminder policy."
                        if is_demo_rule
                        else "Positive prediction passed configured reminder policy."
                    )
        return {
            "target": target,
            "eligible": eligible,
            "reason": reason,
            "decided_at": current.isoformat(),
            "source": "simulated_demo" if is_demo_rule else "model",
            "decision_kind": "rule_based" if is_demo_rule else "model",
            "model_id": prediction.get("model_id"),
            "model_version": prediction.get("model_version"),
            "prediction_score": prediction.get("probability"),
            "data_quality": prediction.get("data_quality", "not_applicable_demo" if is_demo_rule else "unknown"),
            "observed_at": observed.isoformat(),
            "data_freshness_minutes": round(max(0.0, (current - observed).total_seconds() / 60), 2),
        }


reminder_policy = ReminderPolicy()
