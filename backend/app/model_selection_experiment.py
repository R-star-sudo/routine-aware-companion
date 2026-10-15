"""Validation-only model and probability-threshold selection experiments."""

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.tree import DecisionTreeClassifier

from app.dataset_generator import LABEL_COLUMNS
from app.model_evaluation import (
    DEFAULT_DATASET_PATH,
    _calculate_metrics,
    _class_counts,
    _prevalence_baseline,
    chronological_group_split,
    load_dataset,
)

THRESHOLDS = (0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 0.90)


@dataclass(frozen=True)
class CandidateConfig:
    name: str
    max_depth: int
    min_samples_leaf: int
    class_weight: str | None


CANDIDATES = (
    CandidateConfig("baseline", 4, 20, "balanced"),
    CandidateConfig("shallower", 3, 20, "balanced"),
    CandidateConfig("deeper", 5, 20, "balanced"),
    CandidateConfig("larger_leaf_50", 4, 50, "balanced"),
    CandidateConfig("larger_leaf_100", 4, 100, "balanced"),
    CandidateConfig("unweighted", 4, 20, None),
)

DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "model_evaluation"
    / "experiments"
    / "validation_selection_20261014"
)


def _build_candidate(config: CandidateConfig, seed: int) -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            (
                "classifier",
                DecisionTreeClassifier(
                    max_depth=config.max_depth,
                    min_samples_leaf=config.min_samples_leaf,
                    class_weight=config.class_weight,
                    random_state=seed,
                ),
            ),
        ]
    )


def _metrics_at_threshold(
    actual: np.ndarray, scores: np.ndarray, threshold: float
) -> dict[str, Any]:
    metrics = _calculate_metrics(actual, (scores >= threshold).astype(int), scores)
    true_negative, false_positive, false_negative, true_positive = (
        int(value) for value in np.asarray(metrics["confusion_matrix"]).ravel()
    )
    actual_positive = true_positive + false_negative
    actual_negative = true_negative + false_positive
    if actual_positive and actual_negative:
        sensitivity = true_positive / actual_positive
        specificity = true_negative / actual_negative
        metrics["g_mean"] = math.sqrt(sensitivity * specificity)
    else:
        metrics["g_mean"] = None
    return metrics


def _best_threshold(
    operating_points: list[dict[str, Any]],
) -> dict[str, Any]:
    eligible = [point for point in operating_points if point["metrics"]["g_mean"] is not None]
    if not eligible:
        raise ValueError(
            "validation partition must contain both classes to select a threshold"
        )
    return max(
        eligible,
        key=lambda point: (
            point["metrics"]["g_mean"],
            point["metrics"]["precision"]
            if point["metrics"]["precision"] is not None
            else -1.0,
            -abs(point["threshold"] - 0.5),
            point["threshold"],
        ),
    )


def _metrics_for_model(
    model: Pipeline, features: np.ndarray, labels: np.ndarray
) -> dict[str, Any]:
    predictions = model.predict(features).astype(int)
    scores = model.predict_proba(features)[:, 1]
    return _calculate_metrics(labels, predictions, scores)


def _select_target(
    features: np.ndarray,
    labels: np.ndarray,
    train_indices: np.ndarray,
    validation_indices: np.ndarray,
    *,
    seed: int,
) -> tuple[dict[str, Any], dict[str, Pipeline]]:
    train_labels = labels[train_indices].astype(int)
    validation_labels = labels[validation_indices].astype(int)
    if len(train_labels) == 0 or len(validation_labels) == 0:
        raise ValueError("target needs labeled rows in train and validation partitions")
    if len(np.unique(train_labels)) < 2:
        raise ValueError("target training partition contains only one class")
    if len(np.unique(validation_labels)) < 2:
        raise ValueError(
            "target validation partition needs both classes for average-precision selection"
        )

    baseline = _prevalence_baseline(train_labels, validation_labels)
    candidates: list[dict[str, Any]] = []
    models: dict[str, Pipeline] = {}
    for config in CANDIDATES:
        model = _build_candidate(config, seed)
        model.fit(features[train_indices], train_labels)
        models[config.name] = model
        scores = model.predict_proba(features[validation_indices])[:, 1]
        points = [
            {
                "threshold": threshold,
                "metrics": _metrics_at_threshold(
                    validation_labels, scores, threshold
                ),
            }
            for threshold in THRESHOLDS
        ]
        best_point = _best_threshold(points)
        candidates.append(
            {
                "config": asdict(config),
                "average_precision": points[0]["metrics"]["average_precision"],
                "best_validation_g_mean": best_point["metrics"]["g_mean"],
                "operating_points": points,
            }
        )

    selected = max(
        candidates,
        key=lambda candidate: (
            candidate["average_precision"]
            if candidate["average_precision"] is not None
            else -1.0,
            candidate["best_validation_g_mean"],
            -next(
                index
                for index, config in enumerate(CANDIDATES)
                if config.name == candidate["config"]["name"]
            ),
        ),
    )
    selected_point = _best_threshold(selected["operating_points"])
    baseline_config = next(config for config in CANDIDATES if config.name == "baseline")
    original_model = models[baseline_config.name]
    original_metrics = _metrics_for_model(
        original_model, features[validation_indices], validation_labels
    )
    return (
        {
            "class_counts": {
                "train": _class_counts(train_labels),
                "validation": _class_counts(validation_labels),
            },
            "majority_baseline": baseline,
            "original_decision_tree_baseline": original_metrics,
            "candidates": candidates,
            "selected": {
                "config": selected["config"],
                "threshold": selected_point["threshold"],
                "validation_metrics": selected_point["metrics"],
                "selection_rule": (
                    "Highest validation average precision across configurations; "
                    "ties use best validation G-mean. For that configuration, choose "
                    "the listed threshold with highest validation G-mean; ties use "
                    "precision, then proximity to 0.5."
                ),
            },
        },
        models,
    )


def run_experiment(
    dataset_path: Path,
    output_dir: Path,
    *,
    seed: int = 42,
    test_fraction: float = 0.2,
    validation_fraction: float = 0.2,
) -> dict[str, Any]:
    dataset = load_dataset(dataset_path, dataset_mode="synthetic")
    train_indices, validation_indices, test_indices = chronological_group_split(
        dataset,
        test_fraction=test_fraction,
        validation_fraction=validation_fraction,
    )
    results: dict[str, Any] = {
        "experiment": "validation-only decision-tree configuration and threshold selection",
        "dataset": str(dataset_path),
        "dataset_mode": "synthetic",
        "seed": seed,
        "test_fraction": test_fraction,
        "validation_fraction": validation_fraction,
        "split_sequence_counts": {
            "train": len({dataset.groups[index] for index in train_indices}),
            "validation": len(
                {dataset.groups[index] for index in validation_indices}
            ),
            "test": len({dataset.groups[index] for index in test_indices}),
        },
        "feature_allowlist": list(dataset.feature_columns),
        "selection_method": (
            "Configurations are ranked on validation average precision only; "
            "thresholds are chosen by validation G-mean with documented tie-breaks. "
            "The test partition is evaluated only after all target selections are fixed."
        ),
        "thresholds": list(THRESHOLDS),
        "targets": {},
    }
    selected_models: dict[str, Pipeline] = {}
    models_by_target: dict[str, dict[str, Pipeline]] = {}

    for label in LABEL_COLUMNS:
        target_values = dataset.targets[label]
        target_train = np.asarray(
            [index for index in train_indices if target_values[index] is not None],
            dtype=int,
        )
        target_validation = np.asarray(
            [
                index
                for index in validation_indices
                if target_values[index] is not None
            ],
            dtype=int,
        )
        validation, models = _select_target(
            dataset.features,
            target_values,
            target_train,
            target_validation,
            seed=seed,
        )
        selected = validation["selected"]
        selected_model = models[selected["config"]["name"]]
        selected_models[label] = selected_model
        models_by_target[label] = models
        results["targets"][label] = validation

    for label in LABEL_COLUMNS:
        target_values = dataset.targets[label]
        target_test = np.asarray(
            [index for index in test_indices if target_values[index] is not None],
            dtype=int,
        )
        test_labels = target_values[target_test].astype(int)
        if len(test_labels) == 0:
            raise ValueError(f"target {label!r} has no labeled test rows")
        validation = results["targets"][label]
        selected = validation["selected"]
        models = models_by_target[label]
        selected_model = selected_models[label]
        target_train = np.asarray(
            [index for index in train_indices if target_values[index] is not None],
            dtype=int,
        )
        target_validation = np.asarray(
            [
                index
                for index in validation_indices
                if target_values[index] is not None
            ],
            dtype=int,
        )
        baseline_model = models["baseline"]
        selected_scores = selected_model.predict_proba(
            dataset.features[target_test]
        )[:, 1]
        baseline_scores = (
            selected_scores
            if selected_model is baseline_model
            else baseline_model.predict_proba(dataset.features[target_test])[:, 1]
        )
        test_metrics = {
            "class_counts": _class_counts(test_labels),
            "majority_baseline": _prevalence_baseline(
                target_values[target_train].astype(int), test_labels
            )["metrics"],
            "original_decision_tree_baseline": _calculate_metrics(
                test_labels,
                baseline_model.predict(dataset.features[target_test]).astype(int),
                baseline_scores,
            ),
            "selected_candidate": _metrics_at_threshold(
                test_labels, selected_scores, selected["threshold"]
            ),
        }
        validation["class_counts"]["test"] = _class_counts(test_labels)
        validation["class_counts"]["unlabeled"] = {
            "train": int(len(train_indices) - len(target_train)),
            "validation": int(len(validation_indices) - len(target_validation)),
            "test": int(len(test_indices) - len(target_test)),
        }
        validation["class_counts"]["excluded_unlabeled"] = int(
            sum(value is None for value in target_values)
        )
        validation["test_final"] = test_metrics
        results["targets"][label] = validation

    output_dir.mkdir(parents=True, exist_ok=True)
    models_dir = output_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    for label, model in selected_models.items():
        joblib.dump(model, models_dir / f"{label}.joblib")
    (output_dir / "evaluation.json").write_text(
        json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        render_report(results), encoding="utf-8"
    )
    return results


def render_report(results: dict[str, Any]) -> str:
    lines = [
        "# Synthetic Validation-Only Model-Selection Experiment",
        "",
        "> Synthetic-data experiment only. These results do not establish real-world accuracy or justify automatic reminders.",
        "",
        f"- Dataset: `{results['dataset']}`",
        f"- Seed: {results['seed']}",
        f"- Chronological profile-day sequences: `{json.dumps(results['split_sequence_counts'])}`",
        f"- Feature allowlist: {', '.join(results['feature_allowlist'])}",
        f"- Threshold grid: `{json.dumps(results['thresholds'])}`",
        "- Classifier architecture and feature engineering are unchanged; candidate tree settings vary only depth, minimum leaf size, and class weighting.",
        "- Validation selects the configuration by average precision and threshold by G-mean. The final test section contains only fixed selected settings and comparators.",
        "",
    ]
    for label, target in results["targets"].items():
        lines.extend(
            [
                f"## `{label}`",
                "",
                f"- Class counts (negative/positive): `{json.dumps(target['class_counts'])}`",
                f"- Majority-class validation baseline: `{json.dumps(target['majority_baseline'], allow_nan=False)}`",
                f"- Original decision-tree validation baseline (default prediction threshold): `{json.dumps(target['original_decision_tree_baseline'], allow_nan=False)}`",
                "",
                "| Candidate | Threshold | Precision | Recall | F1 | G-mean | AP | FP | FN |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for candidate in target["candidates"]:
            for point in candidate["operating_points"]:
                metrics = point["metrics"]
                matrix = metrics["confusion_matrix"]
                false_positive = matrix[0][1]
                false_negative = matrix[1][0]
                lines.append(
                    "| {name} | {threshold:.2f} | {precision} | {recall} | {f1} | {g_mean} | {ap} | {fp} | {fn} |".format(
                        name=candidate["config"]["name"],
                        threshold=point["threshold"],
                        precision=_format_metric(metrics["precision"]),
                        recall=_format_metric(metrics["recall"]),
                        f1=_format_metric(metrics["f1"]),
                        g_mean=_format_metric(metrics["g_mean"]),
                        ap=_format_metric(metrics["average_precision"]),
                        fp=false_positive,
                        fn=false_negative,
                    )
                )
        selected = target["selected"]
        lines.extend(
            [
                "",
                f"- Selected using validation only: `{json.dumps(selected, allow_nan=False)}`",
                f"- Final held-out test comparison: `{json.dumps(target['test_final'], allow_nan=False)}`",
                "",
            ]
        )
    lines.extend(
        [
            "A lower threshold generally increases predicted events and may reduce missed positives while increasing false alarms; a higher threshold generally does the reverse. Precision, recall, F1, and AP must be read with the class counts and false-positive/false-negative counts because the targets are highly imbalanced.",
            "",
            "These results use synthetic labels and do not establish real-world performance. Do not deploy these models for live reminders without representative real-world validation and appropriate safety review.",
            "",
        ]
    )
    return "\n".join(lines)


def _format_metric(value: float | None) -> str:
    return "undefined" if value is None else f"{value:.4f}"


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare decision-tree settings and thresholds using validation data only."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        results = run_experiment(
            args.input,
            args.output_dir,
            seed=args.seed,
            test_fraction=args.test_fraction,
            validation_fraction=args.validation_fraction,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Validation-selection experiment written to {args.output_dir}", file=sys.stderr)
    print(render_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
