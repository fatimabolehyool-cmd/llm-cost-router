"""Experiment 2, step 1: train a placeholder routing policy and package it for MLServer.

IMPORTANT: the training data here is SYNTHETIC. This experiment measures the serving path
(MLflow -> artifact -> MLServer -> HTTP), not the policy's quality. The real policy is trained
later on junior answers from RouterBench / Experiment 1.

Runs INSIDE the serving container so scikit-learn is the same version for training and serving.
Writes:
  model_repo/routing-policy/model.joblib + model-settings.json   (what MLServer loads)
  mlflow.db + mlartifacts/                                       (MLflow run: params, metrics, artifact)
  results/exp2_inprocess_latency.json                            (baseline: predict without HTTP)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import mlflow
import numpy as np
import sklearn
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.policy_features import FEATURE_VERSION, FEATURES  # noqa: E402

MODEL_DIR = ROOT / "exp2_policy_serving" / "model_repo" / "routing-policy"
MODEL_VERSION = "policy-v0-synthetic"
SEED = 42


def synthetic(n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    task = rng.integers(0, 2, n)            # 0 = choice (4 options), 1 = label (77)
    j1_ok = rng.random(n) < 0.97
    j2_ok = rng.random(n) < 0.97
    agree = (rng.random(n) < 0.6) & j1_ok & j2_ok
    prompt = rng.integers(30, 800, n)
    t1 = rng.integers(1, 300, n)
    t2 = rng.integers(1, 300, n)
    n_opt = np.where(task == 0, 4, 77)
    X = np.column_stack([task == 0, task == 1, np.log(n_opt), agree, j1_ok, j2_ok,
                         np.log1p(prompt), np.log1p(t1), np.log1p(t2)]).astype(np.float32)
    # shape loosely follows the proposal pre-test: ~79% correct when agree, ~47% when not
    p = np.where(agree, 0.79, 0.47) - 0.05 * (task == 1)
    y = (rng.random(n) < p).astype(int)
    return X, y


def main() -> None:
    rng = np.random.default_rng(SEED)
    X, y = synthetic(5000, rng)
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=SEED)
    params = {"n_estimators": 100, "max_depth": 3, "learning_rate": 0.1, "random_state": SEED}
    model = GradientBoostingClassifier(**params).fit(X_tr, y_tr)
    auc = roc_auc_score(y_te, model.predict_proba(X_te)[:, 1])

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_DIR / "model.joblib")
    (MODEL_DIR / "model-settings.json").write_text(json.dumps({
        "name": "routing-policy",
        "implementation": "mlserver_sklearn.SKLearnModel",
        "parameters": {"uri": "./model.joblib", "version": MODEL_VERSION},
    }, indent=2) + "\n")

    # MLflow 3.x refuses the plain-folder backend; use a database backend (SQLite here,
    # PostgreSQL in the full architecture) with artifacts in a local folder.
    mlflow.set_tracking_uri(f"sqlite:///{ROOT / 'mlflow.db'}")
    if mlflow.get_experiment_by_name("exp2-policy-serving") is None:
        mlflow.create_experiment("exp2-policy-serving",
                                 artifact_location=(ROOT / "mlartifacts").as_uri())
    mlflow.set_experiment("exp2-policy-serving")
    with mlflow.start_run(run_name=MODEL_VERSION):
        mlflow.log_params({**params, "feature_version": FEATURE_VERSION,
                           "features": ",".join(FEATURES), "data": "synthetic-5000-seed42",
                           "sklearn_version": sklearn.__version__})
        mlflow.log_metric("test_auc_synthetic", auc)
        mlflow.log_artifacts(str(MODEL_DIR), artifact_path="mlserver_model")

    # Baseline for the ADR: the same prediction in-process, no network hop.
    row = X_te[:1]
    for _ in range(50):
        model.predict_proba(row)
    times = []
    for _ in range(1000):
        t0 = time.perf_counter()
        model.predict_proba(row)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    res = {"n": 1000, "p50_ms": times[500], "p95_ms": times[950], "p99_ms": times[990],
           "sklearn": sklearn.__version__, "test_auc_synthetic": auc}
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "exp2_inprocess_latency.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    print(f"model packaged in {MODEL_DIR}")


if __name__ == "__main__":
    main()
