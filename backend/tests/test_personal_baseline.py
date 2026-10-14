from datetime import datetime, timezone

import pytest

from app.services.personal_baseline import summarize_personal_baseline


def test_personal_baseline_uses_explicit_history_and_marks_unsupported_sleep() -> None:
    days = [
        {"local_date": f"2026-10-{day:02d}",
         "wake_at": f"2026-10-{day:02d}T07:0{minute}:00+05:30",
         "activity_by_local_hour": {"8": 1.0 + minute}}
        for day, minute in ((10, 0), (11, 1), (12, 2))
    ]
    result = summarize_personal_baseline(days, timezone_name="+05:30",
                                          at=datetime(2026, 10, 13, tzinfo=timezone.utc))
    assert result["wake_time_baseline_local"] == "07:01"
    assert result["latest_wake_deviation_minutes"] == 1
    assert result["sleep_duration_baseline_hours"] is None
    assert result["activity_by_local_hour_baseline"]["8"] == pytest.approx(2.0)


def test_insufficient_history_and_bad_timezone_are_explicit() -> None:
    result = summarize_personal_baseline([], timezone_name="UTC")
    assert result["status"] == "insufficient_observations"
    assert result["wake_time_baseline_local"] is None
    assert result["sleep_duration_baseline_hours"] is None
    with pytest.raises(ValueError, match="timezone_name"):
        summarize_personal_baseline([], timezone_name="No/Such_Zone")


def test_naive_observation_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        summarize_personal_baseline([{"local_date": "2026-10-10", "wake_at": "2026-10-10T07:00:00"}], timezone_name="UTC")
