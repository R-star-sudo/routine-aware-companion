"""Chronological training and evaluation for synthetic routine datasets."""

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import joblib
import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
)
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline

from app.dataset_generator import LABEL_COLUMNS
from app.models.decision_tree import build_decision_tree, describe_decision_tree

FEATURE_ALLOWLIST = (
    "feature_hour_sin",
    "feature_hour_cos",
    "feature_expected_wake_hour_local",
    "feature_minutes_from_expected_wake",
    "feature_motion_intensity_m_s2",
    "feature_recent_motion_mean_30m_m_s2",
    "feature_minutes_since_pillbox_open",
)
REAL_FEATURE_ALLOWLIST = (
    *FEATURE_ALLOWLIST,
    "feature_room_temperature_c",
)
REAL_PREPARATION_COLUMNS = (
    "preparation_version",
    "preparation_tool",
    "preparation_raw_sha256",
    "preparation_schedule_sha256",
    "preparation_annotation_sha256",
    "quality_mpu6050_valid",
    "quality_max30102_valid",
    "quality_ds18b20_valid",
    "quality_room_temperature_valid",
    "quality_pillbox_observed",
    "quality_observation_complete_30m",
    "quality_observation_complete_60m",
    "quality_room_temperature_complete_30m",
    "quality_pillbox_complete_to_grace",
)
REQUIRED_COLUMNS = (
    "profile_id",
    "sequence_id",
    "source",
    "timestamp_utc",
    *FEATURE_ALLOWLIST,
    *LABEL_COLUMNS,
)
DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "synthetic_datasets"
    / "routine_dataset.csv"
)
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "model_evaluation"
)
MODEL_FACTORY = Callable[[int], Pipeline]
MODEL_VERSION = "routine-baselines-v1"


def build_logistic_regression(random_state: int) -> Pipeline:
    return Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                     ("scale", StandardScaler()),
                     ("classifier", LogisticRegression(class_weight="balanced", max_iter=1000, random_state=random_state))])


def build_random_forest(random_state: int) -> Pipeline:
    return Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                     ("classifier", RandomForestClassifier(n_estimators=200, max_depth=8, min_samples_leaf=10,
                                                           class_weight="balanced_subsample", random_state=random_state, n_jobs=1))])


MODEL_FACTORIES: dict[str, MODEL_FACTORY] = {
    "decision_tree": build_decision_tree,
    "logistic_regression": build_logistic_regression,
    "random_forest": build_random_forest,
}


class DatasetValidationError(ValueError):
    """Raised when a dataset does not meet the evaluation input contract."""


@dataclass(frozen=True)
class Dataset:
    rows: list[dict[str, Any]]
    features: np.ndarray
    targets: dict[str, np.ndarray]
    groups: list[tuple[str, str]]
    timestamps: list[datetime]
    feature_columns: tuple[str, ...]
    provenance: dict[str, str]


def _parse_timestamp(value: str, row_number: int) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DatasetValidationError(
            f"row {row_number}: timestamp_utc is not valid ISO-8601"
        ) from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise DatasetValidationError(
            f"row {row_number}: timestamp_utc must include a timezone"
        )
    return timestamp


def load_dataset(path: Path, dataset_mode: str = "synthetic") -> Dataset:
    if dataset_mode not in {"synthetic", "prepared-real"}:
        raise DatasetValidationError(
            "dataset_mode must be 'synthetic' or 'prepared-real'"
        )
    feature_columns = (
        FEATURE_ALLOWLIST if dataset_mode == "synthetic" else REAL_FEATURE_ALLOWLIST
    )
    required_columns = REQUIRED_COLUMNS
    if dataset_mode == "prepared-real":
        required_columns = (
            "profile_id",
            "sequence_id",
            "source",
            "timestamp_utc",
            *feature_columns,
            *LABEL_COLUMNS,
            *REAL_PREPARATION_COLUMNS,
        )
    try:
        source = path.open("r", newline="", encoding="utf-8")
    except OSError as exc:
        raise DatasetValidationError(f"cannot read dataset {path}: {exc}") from exc

    with source:
        reader = csv.DictReader(source)
        columns = set(reader.fieldnames or ())
        missing = [column for column in required_columns if column not in columns]
        if missing:
            raise DatasetValidationError(
                f"dataset is missing required columns: {', '.join(missing)}"
            )
        if dataset_mode == "prepared-real":
            unexpected_features = sorted(
                column
                for column in columns
                if column.startswith("feature_") and column not in feature_columns
            )
            if unexpected_features:
                raise DatasetValidationError(
                    "prepared-real dataset contains unapproved feature columns: "
                    + ", ".join(unexpected_features)
                )

        rows: list[dict[str, Any]] = []
        feature_rows: list[list[float]] = []
        timestamps: list[datetime] = []
        targets: dict[str, list[int | None]] = {
            label: [] for label in LABEL_COLUMNS
        }
        groups: list[tuple[str, str]] = []
        last_timestamp_by_group: dict[tuple[str, str], datetime] = {}
        provenance: dict[str, str] = {}

        for row_number, row in enumerate(reader, start=2):
            expected_source = "simulated" if dataset_mode == "synthetic" else "hardware"
            if row.get("source") != expected_source:
                raise DatasetValidationError(
                    f"row {row_number}: source must be {expected_source!r}"
                )
            if dataset_mode == "prepared-real":
                if (
                    row.get("preparation_version") != "1.0"
                    or row.get("preparation_tool") != "app.real_data_preparation"
                ):
                    raise DatasetValidationError(
                        f"row {row_number}: missing or unsupported real-data preparation provenance"
                    )
                row_provenance = {
                    name: str(row.get(name, ""))
                    for name in REAL_PREPARATION_COLUMNS[:5]
                }
                for name in (
                    "preparation_raw_sha256",
                    "preparation_schedule_sha256",
                ):
                    if re.fullmatch(r"[0-9a-f]{64}", row_provenance[name]) is None:
                        raise DatasetValidationError(
                            f"row {row_number}: {name} must be a SHA-256 digest"
                        )
                annotation_digest = row_provenance["preparation_annotation_sha256"]
                if annotation_digest != "not-provided" and re.fullmatch(
                    r"[0-9a-f]{64}", annotation_digest
                ) is None:
                    raise DatasetValidationError(
                        f"row {row_number}: preparation_annotation_sha256 must be a SHA-256 digest or 'not-provided'"
                    )
                for name in REAL_PREPARATION_COLUMNS[5:]:
                    if row.get(name) not in {"True", "False", "true", "false", "1", "0"}:
                        raise DatasetValidationError(
                            f"row {row_number}: {name} must be a boolean"
                        )
                if not provenance:
                    provenance = row_provenance
                elif provenance != row_provenance:
                    raise DatasetValidationError(
                        f"row {row_number}: preparation provenance must be consistent across rows"
                    )
            profile_id = (row.get("profile_id") or "").strip()
            sequence_id = (row.get("sequence_id") or "").strip()
            if not profile_id or not sequence_id:
                raise DatasetValidationError(
                    f"row {row_number}: profile_id and sequence_id are required"
                )
            timestamp = _parse_timestamp(row.get("timestamp_utc") or "", row_number)
            group = (profile_id, sequence_id)
            previous_timestamp = last_timestamp_by_group.get(group)
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise DatasetValidationError(
                    f"row {row_number}: timestamps must increase within each sequence"
                )
            last_timestamp_by_group[group] = timestamp

            feature_values: list[float] = []
            for feature in feature_columns:
                raw_value = row.get(feature)
                if raw_value is None or raw_value.strip() == "":
                    feature_values.append(float("nan"))
                    continue
                try:
                    numeric_value = float(raw_value)
                except ValueError as exc:
                    raise DatasetValidationError(
                        f"row {row_number}: {feature} must be numeric or blank"
                    ) from exc
                if not math.isfinite(numeric_value):
                    raise DatasetValidationError(
                        f"row {row_number}: {feature} must be finite"
                    )
                feature_values.append(numeric_value)

            parsed_targets: dict[str, int | None] = {}
            for label in LABEL_COLUMNS:
                raw_label = row.get(label)
                if raw_label is None or raw_label.strip() == "":
                    parsed_targets[label] = None
                elif raw_label.strip() in {"0", "1"}:
                    parsed_targets[label] = int(raw_label)
                else:
                    raise DatasetValidationError(
                        f"row {row_number}: {label} must be 0, 1, or blank"
                    )

            normalized_row = dict(row)
            normalized_row["_timestamp"] = timestamp
            normalized_row["_features"] = feature_values
            normalized_row["_targets"] = parsed_targets
            rows.append(normalized_row)
            feature_rows.append(feature_values)
            timestamps.append(timestamp)
            groups.append(group)
            for label in LABEL_COLUMNS:
                targets[label].append(parsed_targets[label])

    if not rows:
        raise DatasetValidationError("dataset contains no rows")

    return Dataset(
        rows=rows,
        features=np.asarray(feature_rows, dtype=float),
        targets={
            label: np.asarray(values, dtype=object) for label, values in targets.items()
        },
        groups=groups,
        timestamps=timestamps,
        feature_columns=tuple(feature_columns),
        provenance=provenance,
    )


def chronological_group_split(
    dataset: Dataset,
    test_fraction: float = 0.2,
    validation_fraction: float = 0.2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split complete profile-day groups chronologically into three partitions."""
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be greater than 0 and less than 1")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be greater than 0 and less than 1")
    if test_fraction + validation_fraction >= 1:
        raise ValueError("test and validation fractions must sum to less than 1")

    profile_groups: dict[str, dict[str, datetime]] = defaultdict(dict)
    for (profile_id, sequence_id), timestamp in zip(
        dataset.groups, dataset.timestamps
    ):
        existing = profile_groups[profile_id].get(sequence_id)
        if existing is None or timestamp < existing:
            profile_groups[profile_id][sequence_id] = timestamp

    train_groups: set[tuple[str, str]] = set()
    validation_groups: set[tuple[str, str]] = set()
    test_groups: set[tuple[str, str]] = set()
    for profile_id, sequence_dates in profile_groups.items():
        ordered = sorted(sequence_dates.items(), key=lambda item: item[1])
        if len(ordered) < 3:
            raise DatasetValidationError(
                f"profile {profile_id!r} needs at least three profile-day sequences "
                "for chronological train/validation/test evaluation"
            )
        test_count = max(1, math.ceil(len(ordered) * test_fraction))
        validation_count = max(1, math.ceil(len(ordered) * validation_fraction))
        while test_count + validation_count >= len(ordered):
            if test_count >= validation_count and test_count > 1:
                test_count -= 1
            elif validation_count > 1:
                validation_count -= 1
            else:
                break
        if test_count + validation_count >= len(ordered):
            raise DatasetValidationError(
                f"profile {profile_id!r} needs at least three profile-day sequences "
                "for chronological train/validation/test evaluation"
            )
        train_end = len(ordered) - test_count - validation_count
        validation_end = len(ordered) - test_count
        train_groups.update(
            (profile_id, sequence_id) for sequence_id, _ in ordered[:train_end]
        )
        validation_groups.update(
            (profile_id, sequence_id)
            for sequence_id, _ in ordered[train_end:validation_end]
        )
        test_groups.update(
            (profile_id, sequence_id)
            for sequence_id, _ in ordered[validation_end:]
        )

    train_indices = np.asarray(
        [index for index, group in enumerate(dataset.groups) if group in train_groups],
        dtype=int,
    )
    validation_indices = np.asarray(
        [
            index
            for index, group in enumerate(dataset.groups)
            if group in validation_groups
        ],
        dtype=int,
    )
    test_indices = np.asarray(
        [index for index, group in enumerate(dataset.groups) if group in test_groups],
        dtype=int,
    )
    if not len(train_indices) or not len(validation_indices) or not len(test_indices):
        raise DatasetValidationError("chronological split produced an empty partition")
    return train_indices, validation_indices, test_indices


def _class_counts(labels: Sequence[int]) -> dict[str, int]:
    counts = Counter(int(label) for label in labels)
    return {"negative": counts.get(0, 0), "positive": counts.get(1, 0)}


def _calculate_metrics(
    actual: np.ndarray, predicted: np.ndarray, scores: np.ndarray
) -> dict[str, Any]:
    actual_values = actual.astype(int)
    predicted_values = predicted.astype(int)
    matrix = confusion_matrix(actual_values, predicted_values, labels=[0, 1])
    true_negative, false_positive, false_negative, true_positive = (
        int(value) for value in matrix.ravel()
    )
    predicted_positive = true_positive + false_positive
    actual_positive = true_positive + false_negative
    actual_negative = true_negative + false_positive
    precision: float | None = (
        true_positive / predicted_positive if predicted_positive else None
    )
    recall: float | None = true_positive / actual_positive if actual_positive else None
    f1_denominator = 2 * true_positive + false_positive + false_negative
    f1: float | None = (
        2 * true_positive / f1_denominator if f1_denominator else None
    )
    reasons: list[str] = []
    if precision is None:
        reasons.append(
            "precision is undefined because there are no predicted positives"
        )
    if recall is None:
        reasons.append(
            "recall is undefined because there are no actual positive examples"
        )
    if f1 is None:
        reasons.append(
            "F1 is undefined because there are no actual or predicted positives"
        )

    average_precision: float | None = None
    if actual_positive and actual_negative:
        average_precision = float(average_precision_score(actual_values, scores))
    else:
        reasons.append(
            "average precision is unavailable because the test set needs both classes"
        )
    if not actual_negative:
        reasons.append("specificity is undefined because there are no actual negatives")

    calibration_bins = []
    expected_calibration_error = None
    if len(actual_values):
        total = len(actual_values)
        errors = []
        for index in range(10):
            lower = index / 10
            upper = (index + 1) / 10
            mask = (scores >= lower) & ((scores < upper) if index < 9 else (scores <= upper))
            if np.any(mask):
                observed_rate = float(np.mean(actual_values[mask]))
                mean_score = float(np.mean(scores[mask]))
                count = int(np.sum(mask))
                calibration_bins.append({"lower": lower, "upper": upper, "count": count,
                                         "mean_probability": mean_score, "observed_rate": observed_rate})
                errors.append(count / total * abs(mean_score - observed_rate))
        expected_calibration_error = float(sum(errors))

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "confusion_matrix": matrix.tolist(),
        "average_precision": average_precision,
        "brier_score": float(brier_score_loss(actual_values, scores)) if len(actual_values) else None,
        "expected_calibration_error_10_bins": expected_calibration_error,
        "calibration_bins_10": calibration_bins,
        "undefined_metric_notes": reasons,
    }


def _prevalence_baseline(
    train_labels: np.ndarray, test_labels: np.ndarray
) -> dict[str, Any]:
    prevalence = float(np.mean(train_labels))
    prediction = int(prevalence >= 0.5)
    predictions = np.full(len(test_labels), prediction, dtype=int)
    return {
        "training_prevalence": prevalence,
        "prediction_threshold": 0.5,
        "predicted_class": prediction,
        "metrics": _calculate_metrics(
            test_labels, predictions, np.full(len(test_labels), prevalence)
        ),
    }


def _evaluate_target(
    dataset: Dataset,
    label: str,
    train_indices: np.ndarray,
    validation_indices: np.ndarray,
    test_indices: np.ndarray,
    model_factory: MODEL_FACTORY,
    seed: int,
    output_dir: Path | None,
) -> dict[str, Any]:
    labels = dataset.targets[label]
    labeled_indices = np.asarray(
        [i for i, value in enumerate(labels) if value is not None], dtype=int
    )
    train_target_indices = np.intersect1d(train_indices, labeled_indices)
    validation_target_indices = np.intersect1d(validation_indices, labeled_indices)
    test_target_indices = np.intersect1d(test_indices, labeled_indices)
    y_train = labels[train_target_indices].astype(int)
    y_test = labels[test_target_indices].astype(int)

    result: dict[str, Any] = {
        "label": label,
        "features": list(dataset.feature_columns),
        "class_counts": {
            "train": _class_counts(y_train),
            "validation": _class_counts(
                labels[validation_target_indices].astype(int)
            ),
            "test": _class_counts(y_test),
            "unlabeled": {
                "train": int(len(train_indices) - len(train_target_indices)),
                "validation": int(
                    len(validation_indices) - len(validation_target_indices)
                ),
                "test": int(len(test_indices) - len(test_target_indices)),
            },
            "excluded_unlabeled": int(len(labels) - len(labeled_indices)),
        },
        "train_rows": int(len(y_train)),
        "test_rows": int(len(y_test)),
        "baseline": None,
        "decision_tree": None,
        "status": "evaluated",
        "limitations": [],
    }
    if len(y_train) == 0 or len(y_test) == 0:
        result["status"] = "inadequate_data"
        result["limitations"].append(
            "No labeled rows in the train or test partition; metrics cannot be calculated."
        )
        return result

    result["baseline"] = _prevalence_baseline(y_train, y_test)
    if len(np.unique(y_train)) < 2:
        result["status"] = "inadequate_data"
        result["limitations"].append(
            "Training partition contains only one class; decision tree was not fitted."
        )
        return result
    if len(np.unique(y_test)) < 2:
        result["limitations"].append(
            "Test partition contains only one class; average precision is unavailable."
        )

    factories = MODEL_FACTORIES if model_factory is build_decision_tree else {"decision_tree": model_factory}
    models_dir = output_dir / "models" if output_dir is not None else None
    if models_dir is not None:
        models_dir.mkdir(parents=True, exist_ok=True)
    for model_name, factory in factories.items():
        model = factory(seed)
        model.fit(dataset.features[train_target_indices], y_train)
        validation_y = labels[validation_target_indices].astype(int)
        if len(validation_target_indices):
            validation_scores = model.predict_proba(dataset.features[validation_target_indices])[:, 1]
            validation_predictions = (validation_scores >= 0.5).astype(int)
            validation_metrics = _calculate_metrics(validation_y, validation_predictions, validation_scores)
        else:
            validation_metrics = None
        predictions = model.predict(dataset.features[test_target_indices]).astype(int)
        scores = model.predict_proba(dataset.features[test_target_indices])[:, 1]
        metrics = _calculate_metrics(y_test, predictions, scores)
        metrics["false_positives_per_day"] = metrics["confusion_matrix"][0][1] / max(
            1, len({dataset.groups[index] for index in test_target_indices})
        )
        result[model_name] = {
            "model": describe_decision_tree() if model_name == "decision_tree" else model_name,
            "validation_metrics": validation_metrics,
            "metrics": metrics,
        }
        if models_dir is not None:
            # The envelope makes the feature and target contract inspectable alongside the estimator.
            definition = {
                "label_unusual_wake_within_60m": "An unusual wake event in the next 60 minutes.",
                "label_delayed_medication_access_within_30m": "Delayed pillbox access event in the next 30 minutes; not proof medication was missed.",
                "label_room_temp_out_of_range_within_30m": "Room temperature outside configured synthetic range in the next 30 minutes.",
            }[label]
            artifact_name = f"{label}.joblib" if model_name == "decision_tree" else f"{label}.{model_name}.joblib"
            joblib.dump({"artifact_version": MODEL_VERSION, "target": label,
                         "feature_names": list(dataset.feature_columns), "target_definition": definition,
                         "dataset_mode": "synthetic-only" if dataset.provenance == {} else "prepared-real",
                         "estimator": model}, models_dir / artifact_name)
    result["decision_tree"] = result.get("decision_tree")
    return result


def evaluate_dataset(
    dataset_path: Path,
    *,
    test_fraction: float = 0.2,
    validation_fraction: float = 0.2,
    seed: int = 42,
    dataset_mode: str = "synthetic",
    output_dir: Path | None = None,
    model_factory: MODEL_FACTORY = build_decision_tree,
) -> dict[str, Any]:
    dataset = load_dataset(dataset_path, dataset_mode=dataset_mode)
    train_indices, validation_indices, test_indices = chronological_group_split(
        dataset, test_fraction, validation_fraction
    )
    results: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "dataset": str(dataset_path),
        "dataset_mode": dataset_mode,
        "seed": seed,
        "test_fraction": test_fraction,
        "validation_fraction": validation_fraction,
        "split_strategy": (
            "Chronological within each profile: latest complete profile-day "
            "sequences are test, immediately preceding complete sequences are "
            "validation, and earlier complete sequences are train. Sequences "
            "never cross partitions."
        ),
        "feature_allowlist": list(dataset.feature_columns),
        "provenance": dataset.provenance,
        "split_sequence_counts": {
            "train": len({dataset.groups[index] for index in train_indices}),
            "validation": len(
                {dataset.groups[index] for index in validation_indices}
            ),
            "test": len({dataset.groups[index] for index in test_indices}),
        },
        "labels": {},
    }
    for label in LABEL_COLUMNS:
        results["labels"][label] = _evaluate_target(
            dataset,
            label,
            train_indices,
            validation_indices,
            test_indices,
            model_factory,
            seed,
            output_dir,
        )
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        results_path = output_dir / "evaluation.json"
        results_path.write_text(
            json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        (output_dir / "report.md").write_text(
            render_report(results), encoding="utf-8"
        )
    return results


def render_report(results: dict[str, Any]) -> str:
    is_real = results.get("dataset_mode", "synthetic") == "prepared-real"
    caveat = (
        "> Prepared real-data evaluation is not evidence of real-world accuracy "
        "without representative, independently validated observations."
        if is_real
        else "> Results from synthetic data are for pipeline development only; "
        "they do not establish real-world predictive accuracy."
    )
    lines = [
        (
            "# Prepared Real-Data Model Evaluation"
            if is_real
            else "# Synthetic Routine Model Evaluation"
        ),
        "",
        caveat,
        "",
        f"- Dataset: `{results['dataset']}`",
        f"- Seed: `{results['seed']}`",
        f"- Split: {results['split_strategy']}",
        f"- Features: {', '.join(results['feature_allowlist'])}",
        "",
    ]
    if results.get("provenance"):
        lines.append(f"- Preparation provenance: `{json.dumps(results['provenance'])}`")
        lines.append("")
    lines.extend(
        [
        "Undefined precision, recall, or F1 metrics are reported as null and "
        "explained below. "
        "Average precision is null when the test set lacks either class.",
        "",
        ]
    )
    for label, result in results["labels"].items():
        lines.extend(
            [
                f"## `{label}`",
                "",
                f"- Status: **{result['status']}**",
                f"- Train class counts: `{json.dumps(result['class_counts']['train'])}`",
                f"- Validation class counts: "
                f"`{json.dumps(result['class_counts']['validation'])}`",
                f"- Test class counts: `{json.dumps(result['class_counts']['test'])}`",
                f"- Unlabeled rows excluded: {result['class_counts']['excluded_unlabeled']}",
            ]
        )
        baseline = result.get("baseline")
        if baseline:
            lines.append(
                f"- Training-prevalence baseline ({baseline['predicted_class']} "
                f"class at 0.5 threshold): "
                f"`{json.dumps(baseline['metrics'], allow_nan=False)}`"
            )
        for model_name in MODEL_FACTORIES:
            model_result = result.get(model_name)
            if model_result:
                lines.append(
                    f"- {model_name.replace('_', ' ').title()} validation metrics: "
                    f"`{json.dumps(model_result.get('validation_metrics'), allow_nan=False)}`"
                )
                lines.append(
                    f"- {model_name.replace('_', ' ').title()} held-out test metrics: "
                    f"`{json.dumps(model_result['metrics'], allow_nan=False)}`"
                )
        for limitation in result["limitations"]:
            lines.append(f"- Limitation: {limitation}")
        lines.append("")
    lines.extend(
        [
            "Evaluation must split by complete profile-day sequences to avoid "
            "leakage across neighboring observations. "
            + (
                "Prepared observations still require independent ground-truth "
                "review and cannot by themselves establish predictive accuracy."
                if is_real
                else "Synthetic values cannot replace validation on representative "
                "real-world data."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Train and evaluate per-target models on synthetic or explicitly "
            "prepared routine CSV data."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--dataset-mode",
        choices=("synthetic", "prepared-real"),
        default="synthetic",
        help="Input provenance mode; prepared-real must be explicitly selected",
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.test_fraction) or not 0 < args.test_fraction < 1:
        parser.error("--test-fraction must be greater than 0 and less than 1")
    if (
        not math.isfinite(args.validation_fraction)
        or not 0 < args.validation_fraction < 1
    ):
        parser.error("--validation-fraction must be greater than 0 and less than 1")
    if args.test_fraction + args.validation_fraction >= 1:
        parser.error("--test-fraction and --validation-fraction must sum to less than 1")
    try:
        results = evaluate_dataset(
            args.input,
            test_fraction=args.test_fraction,
            validation_fraction=args.validation_fraction,
            seed=args.seed,
            dataset_mode=args.dataset_mode,
            output_dir=args.output_dir,
        )
    except (DatasetValidationError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Evaluation artifacts written to {args.output_dir}", file=sys.stderr)
    print(render_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
