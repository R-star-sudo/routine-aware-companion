import csv
import json
from datetime import date, timezone
from pathlib import Path

import joblib
import numpy as np

from app.dataset_generator import CSV_COLUMNS, LABEL_COLUMNS, generate_dataset
from app.model_evaluation import chronological_group_split, load_dataset
from app.model_selection_experiment import (
    CANDIDATES,
    THRESHOLDS,
    _best_threshold,
    _metrics_at_threshold,
    run_experiment,
)


def test_threshold_selection_uses_g_mean_then_precision() -> None:
    points = [
        {"threshold": 0.1, "metrics": {"g_mean": 0.7, "precision": 0.1}},
        {"threshold": 0.5, "metrics": {"g_mean": 0.8, "precision": 0.05}},
        {"threshold": 0.7, "metrics": {"g_mean": 0.8, "precision": 0.2}},
    ]

    assert _best_threshold(points) == points[2]


def test_threshold_metrics_include_confusion_matrix_average_precision_and_g_mean() -> None:
    metrics = _metrics_at_threshold(
        np.asarray([0, 0, 1, 1]),
        np.asarray([0.1, 0.8, 0.6, 0.9]),
        0.5,
    )

    assert metrics["confusion_matrix"] == [[1, 1], [0, 2]]
    assert metrics["precision"] == 2 / 3
    assert metrics["recall"] == 1
    assert metrics["f1"] == 0.8
    assert metrics["average_precision"] is not None
    assert metrics["g_mean"] == np.sqrt(0.5)


def test_experiment_selects_on_validation_and_reports_test_separately(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "routine.csv"
    output_dir = tmp_path / "experiments"
    rows = list(
        generate_dataset(
            days=60,
            interval_seconds=3600,
            seed=20261014,
            scenario_rate=0.15,
            start_date=date(2026, 8, 1),
            local_timezone=timezone.utc,
        )
    )
    # Ensure every chronological partition has both classes for this workflow test.
    for row in rows:
        if row["profile_id"] == "synthetic-profile-01" and row["day_index"] in {
            5,
            40,
            55,
        }:
            for label in LABEL_COLUMNS:
                row[label] = 1
    with dataset_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    results = run_experiment(
        dataset_path,
        output_dir,
        seed=42,
        test_fraction=0.2,
        validation_fraction=0.2,
    )

    assert results["dataset_mode"] == "synthetic"
    assert results["selection_method"].startswith("Configurations are ranked on validation")
    assert results["split_sequence_counts"] == {
        "train": 108,
        "validation": 36,
        "test": 36,
    }
    assert set(results["targets"]) == set(LABEL_COLUMNS)
    for target in results["targets"].values():
        assert len(target["candidates"]) == len(CANDIDATES)
        assert all(
            len(candidate["operating_points"]) == len(THRESHOLDS)
            for candidate in target["candidates"]
        )
        assert set(target["test_final"]) == {
            "class_counts",
            "majority_baseline",
            "original_decision_tree_baseline",
            "selected_candidate",
        }
        assert target["selected"]["threshold"] in THRESHOLDS
        assert target["test_final"]["selected_candidate"]["confusion_matrix"]

    saved = json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert saved == results
    assert (output_dir / "report.md").is_file()
    assert len(list((output_dir / "models").glob("*.joblib"))) == len(LABEL_COLUMNS)

    dataset = load_dataset(dataset_path)
    _, _, test_indices = chronological_group_split(dataset)
    for label, target in results["targets"].items():
        labels = dataset.targets[label]
        target_test = np.asarray(
            [index for index in test_indices if labels[index] is not None],
            dtype=int,
        )
        model = joblib.load(output_dir / "models" / f"{label}.joblib")
        scores = model.predict_proba(dataset.features[target_test])[:, 1]
        expected = _metrics_at_threshold(
            labels[target_test].astype(int),
            scores,
            target["selected"]["threshold"],
        )
        assert (
            target["test_final"]["selected_candidate"]["confusion_matrix"]
            == expected["confusion_matrix"]
        )
        assert (
            target["test_final"]["selected_candidate"]["average_precision"]
            == expected["average_precision"]
        )
