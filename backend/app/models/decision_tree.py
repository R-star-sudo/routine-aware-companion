"""Small decision-tree baseline for offline routine-event evaluation."""

from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.tree import DecisionTreeClassifier


def build_decision_tree(random_state: int) -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            (
                "classifier",
                DecisionTreeClassifier(
                    max_depth=4,
                    min_samples_leaf=20,
                    class_weight="balanced",
                    random_state=random_state,
                ),
            ),
        ]
    )


def describe_decision_tree() -> str:
    return (
        "DecisionTreeClassifier(max_depth=4, min_samples_leaf=20, "
        "class_weight='balanced') with training-only median imputation"
    )
