import csv
import json
from datetime import date, timezone
from pathlib import Path

import numpy as np
import pytest
from sklearn.metrics import average_precision_score, auc, precision_recall_curve

from app.dataset_generator import CSV_COLUMNS, generate_dataset
from app.model_evaluation import (
    FEATURE_ALLOWLIST,
    DatasetValidationError,
    _calculate_metrics,
    _prevalence_baseline,
    chronological_group_split,
    evaluate_dataset,
    load_dataset,
    render_report,
)


def write_dataset(
    path: Path,
    *,
    days: int = 8,
    interval_seconds: int = 3600,
    scenario_rate: float = 1.0,
) -> None:
    rows = generate_dataset(
        days,
        interval_seconds,
        seed=1701,
        scenario_rate=scenario_rate,
        start_date=date(2026, 1, 1),
        local_timezone=timezone.utc,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_dataset_validation_and_explicit_allowlist(tmp_path: Path) -> None:
    path = tmp_path / "dataset.csv"
    write_dataset(path)
    fields, rows = read_rows(path)
    assert set(FEATURE_ALLOWLIST).issubset(fields)

    fields.append("metadata_leak")
    for row in rows:
        row["metadata_leak"] = "999999"
    write_rows(path, fields, rows)

    dataset = load_dataset(path)
    assert dataset.features.shape[1] == len(FEATURE_ALLOWLIST)
    assert not any(name.startswith("label_") for name in FEATURE_ALLOWLIST)
    assert not any(name.startswith("metadata_") for name in FEATURE_ALLOWLIST)
    assert not {"profile_id", "sequence_id", "timestamp_utc"}.intersection(
        FEATURE_ALLOWLIST
    )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("source", "hardware", "source must be 'simulated'"),
        ("label_unusual_wake_within_60m", "2", "must be 0, 1, or blank"),
        ("feature_hour_sin", "nan", "must be finite"),
    ],
)
def test_invalid_input_values_are_rejected(
    tmp_path: Path, column: str, value: str, message: str
) -> None:
    path = tmp_path / "dataset.csv"
    write_dataset(path, days=2)
    fields, rows = read_rows(path)
    rows[0][column] = value
    write_rows(path, fields, rows)

    with pytest.raises(DatasetValidationError, match=message):
        load_dataset(path)


def test_missing_required_columns_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "incomplete.csv"
    path.write_text("profile_id,sequence_id\nprofile,day\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="missing required columns"):
        load_dataset(path)


def test_chronological_split_keeps_profile_day_groups_intact_and_is_deterministic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dataset.csv"
    write_dataset(path, days=8)
    dataset = load_dataset(path)
    train_a, validation_a, test_a = chronological_group_split(
        dataset, test_fraction=0.25
    )
    train_b, validation_b, test_b = chronological_group_split(
        dataset, test_fraction=0.25
    )

    assert np.array_equal(train_a, train_b)
    assert np.array_equal(validation_a, validation_b)
    assert np.array_equal(test_a, test_b)
    train_groups = {dataset.groups[index] for index in train_a}
    validation_groups = {dataset.groups[index] for index in validation_a}
    test_groups = {dataset.groups[index] for index in test_a}
    assert not train_groups.intersection(validation_groups)
    assert not train_groups.intersection(test_groups)
    assert not validation_groups.intersection(test_groups)
    for profile_id in {group[0] for group in dataset.groups}:
        train_times = [
            dataset.timestamps[index]
            for index in train_a
            if dataset.groups[index][0] == profile_id
        ]
        test_times = [
            dataset.timestamps[index]
            for index in test_a
            if dataset.groups[index][0] == profile_id
        ]
        validation_times = [
            dataset.timestamps[index]
            for index in validation_a
            if dataset.groups[index][0] == profile_id
        ]
        assert max(train_times) < min(test_times)
        assert max(train_times) < min(validation_times)
        assert max(validation_times) < min(test_times)


def test_split_requires_more_than_one_sequence_per_profile(tmp_path: Path) -> None:
    path = tmp_path / "dataset.csv"
    write_dataset(path, days=1)

    with pytest.raises(DatasetValidationError, match="at least three profile-day"):
        chronological_group_split(load_dataset(path))


def test_missing_features_are_imputed_and_artifacts_are_written(tmp_path: Path) -> None:
    path = tmp_path / "dataset.csv"
    output_dir = tmp_path / "artifacts"
    write_dataset(path, days=14, interval_seconds=300)
    fields, rows = read_rows(path)
    for row in rows:
        row["feature_recent_motion_mean_30m_m_s2"] = ""
        row["feature_minutes_since_pillbox_open"] = ""
    write_rows(path, fields, rows)

    results = evaluate_dataset(path, seed=77, output_dir=output_dir)

    assert results["feature_allowlist"] == list(FEATURE_ALLOWLIST)
    assert (output_dir / "evaluation.json").is_file()
    assert (output_dir / "report.md").is_file()
    assert (output_dir / "models").is_dir()
    saved_results = json.loads((output_dir / "evaluation.json").read_text())
    assert saved_results["seed"] == 77
    for result in results["labels"].values():
        assert result["decision_tree"] is not None
        assert result["decision_tree"]["metrics"]["confusion_matrix"]
        assert result["logistic_regression"]["metrics"]["brier_score"] is not None
        assert result["random_forest"]["metrics"]["false_positives_per_day"] >= 0
        assert result["decision_tree"]["validation_metrics"]
    import joblib
    artifact = joblib.load(output_dir / "models" / "label_unusual_wake_within_60m.joblib")
    assert artifact["feature_names"] == list(FEATURE_ALLOWLIST)
    assert artifact["target"] == "label_unusual_wake_within_60m"
    assert artifact["artifact_version"]


def test_versioned_artifact_contract_mismatch_is_rejected(monkeypatch, tmp_path: Path) -> None:
    import joblib
    import app.services.inference_service as inference

    target = "label_unusual_wake_within_60m"
    joblib.dump({"artifact_version": "old", "target": target,
                 "feature_names": list(FEATURE_ALLOWLIST), "estimator": object()}, tmp_path / f"{target}.joblib")
    monkeypatch.setattr(inference, "ARTIFACT_DIR", tmp_path)
    inference._load_model.cache_clear()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        inference._load_model(target)
    assert error.value.status_code == 503
    inference._load_model.cache_clear()


def test_prevalence_baseline_handles_imbalanced_training_data() -> None:
    baseline = _prevalence_baseline(
        np.asarray([0, 0, 0, 0, 1]), np.asarray([0, 1, 1])
    )

    assert baseline["training_prevalence"] == pytest.approx(0.2)
    assert baseline["predicted_class"] == 0
    assert baseline["metrics"]["confusion_matrix"] == [[1, 0], [2, 0]]
    assert baseline["metrics"]["precision"] is None
    assert baseline["metrics"]["recall"] == 0
    assert baseline["metrics"]["f1"] == 0


def test_single_class_test_metrics_mark_undefined_average_precision_and_recall() -> None:
    metrics = _calculate_metrics(
        np.asarray([0, 0]),
        np.asarray([0, 0]),
        np.asarray([0.1, 0.2]),
    )

    assert metrics["average_precision"] is None
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None
    assert any("no actual positive examples" in note for note in metrics["undefined_metric_notes"])
    assert any("no predicted positives" in note for note in metrics["undefined_metric_notes"])
    assert any(
        "average precision is unavailable" in note
        for note in metrics["undefined_metric_notes"]
    )
    report = render_report(
        {
            "dataset": "synthetic.csv",
            "seed": 1,
            "split_strategy": "chronological",
            "feature_allowlist": list(FEATURE_ALLOWLIST),
            "labels": {
                "target": {
                    "status": "evaluated",
                    "class_counts": {
                        "train": {"negative": 1, "positive": 1},
                        "validation": {"negative": 1, "positive": 1},
                        "test": {"negative": 2, "positive": 0},
                        "excluded_unlabeled": 0,
                    },
                    "baseline": {
                        "predicted_class": 0,
                        "metrics": metrics,
                    },
                    "decision_tree": None,
                    "limitations": [],
                }
            },
        }
    )
    assert '"precision": null' in report
    assert '"recall": null' in report
    assert "None" not in report


def test_no_positive_predictions_explain_undefined_precision() -> None:
    metrics = _calculate_metrics(
        np.asarray([0, 1]),
        np.asarray([0, 0]),
        np.asarray([0.1, 0.2]),
    )

    assert metrics["precision"] is None
    assert metrics["recall"] == 0
    assert metrics["f1"] == 0
    assert metrics["average_precision"] is not None
    assert any("no predicted positives" in note for note in metrics["undefined_metric_notes"])


def test_missing_negative_class_has_undefined_average_precision_but_defined_precision_recall() -> None:
    metrics = _calculate_metrics(
        np.asarray([1, 1]),
        np.asarray([1, 0]),
        np.asarray([0.9, 0.1]),
    )

    assert metrics["precision"] == 1
    assert metrics["recall"] == 0.5
    assert metrics["f1"] == pytest.approx(2 / 3)
    assert metrics["average_precision"] is None
    assert any("no actual negatives" in note for note in metrics["undefined_metric_notes"])


def test_average_precision_is_not_trapezoidal_precision_recall_area() -> None:
    actual = np.asarray([0, 0, 1, 0])
    scores = np.asarray([0.1, 0.4, 0.35, 0.8])
    precision_curve, recall_curve, _ = precision_recall_curve(actual, scores)
    trapezoidal_area = auc(recall_curve, precision_curve)

    metrics = _calculate_metrics(
        actual, np.asarray([0, 0, 0, 1]), scores
    )

    assert metrics["average_precision"] == pytest.approx(
        average_precision_score(actual, scores)
    )
    assert metrics["average_precision"] != pytest.approx(trapezoidal_area)


def test_single_class_training_set_reports_inadequate_data(tmp_path: Path) -> None:
    path = tmp_path / "dataset.csv"
    write_dataset(path, days=4, interval_seconds=3600, scenario_rate=0)

    results = evaluate_dataset(path)

    for result in results["labels"].values():
        assert result["status"] == "inadequate_data"
        assert result["decision_tree"] is None
        assert result["limitations"]
