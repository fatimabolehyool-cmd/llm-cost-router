"""Re-validation pipeline (the "training job" of the professor's slide, as a CLI — ADR-15).

Contract (interface I8):
  input   --data <replay jsonl>  --window <label>  (+ MLFLOW_TRACKING_URI in the environment)
  output  one MLflow run (params, metrics, artifacts), one new registered version of
          "routing-policy" with alias `challenger`, and a JSON summary on stdout / in results/.
  exit    0 = run completed (gate passed OR failed — both are valid outcomes); non-zero = error.
It never moves the `champion` alias: promotion needs a human (pipelines.registry promote).

Steps:
  1. load the replay data and record its SHA-256 (data version)
  2. split by GROUP hash into train / validation / release-evaluation / test (feedback 9);
     the test split is never used here
  3. champion = rule-v0 (accept iff juniors agree); challenger = logistic regression on
     policy-features-v1, threshold chosen on validation to match the rule's coverage
  4. compare both on release-evaluation: accuracy of accepted answers, coverage, and the
     upper 95% bound of the false-acceptance rate
  5. gate: challenger accuracy >= champion accuracy, coverage within 2 points, false-acceptance
     upper bound <= 25%
  6. log everything to MLflow, register the model, set alias `challenger`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import joblib
import mlflow
import numpy as np
from mlflow import MlflowClient
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.policy_features import FEATURE_VERSION, FEATURES, build  # noqa: E402

MODEL_NAME = "routing-policy"
EXPERIMENT = "policy-revalidation"
SPLITS = (("train", 0.60), ("validation", 0.15), ("release_eval", 0.15), ("test", 0.10))


def split_of(group: str) -> str:
    """Deterministic split by hash of the group key, so near-duplicates never straddle splits."""
    u = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    acc = 0.0
    for name, share in SPLITS:
        acc += share
        if u <= acc:
            return name
    return SPLITS[-1][0]


def features(r: dict) -> list[float]:
    return build(
        r["task_type"],
        r["n_options"],
        r["agreed"],
        r["j1_ok"],
        r["j2_ok"],
        r["prompt_tokens"],
        r["j1_tokens"],
        r["j2_tokens"],
    )


def wilson_upper(errors: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = errors / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre + margin) / (1 + z * z / n)


def evaluate(accept: np.ndarray, y: np.ndarray) -> dict:
    n_acc = int(accept.sum())
    correct = int((y[accept] == 1).sum()) if n_acc else 0
    return {
        "coverage": n_acc / len(y),
        "accepted_accuracy": correct / n_acc if n_acc else 0.0,
        "false_accept_rate": 1 - correct / n_acc if n_acc else 1.0,
        "false_accept_upper95": wilson_upper(n_acc - correct, n_acc),
        "n": len(y),
        "n_accepted": n_acc,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument(
        "--window", required=True, help="label of the replayed time window, e.g. 2026-W43"
    )
    ap.add_argument("--out", default=str(ROOT / "results"))
    args = ap.parse_args()

    raw = Path(args.data).read_bytes()
    data_sha = hashlib.sha256(raw).hexdigest()
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    by_split: dict[str, list[dict]] = {name: [] for name, _ in SPLITS}
    for r in rows:
        by_split[split_of(r["group"])].append(r)

    def xy(name):
        part = by_split[name]
        return (
            np.array([features(r) for r in part], dtype=np.float32),
            np.array([r["junior_correct"] for r in part]),
            np.array([bool(r["agreed"]) for r in part]),
        )

    Xtr, ytr, _ = xy("train")
    Xva, yva, agree_va = xy("validation")
    Xre, yre, agree_re = xy("release_eval")

    model = LogisticRegression(max_iter=1000, C=1.0).fit(Xtr, ytr)
    # threshold on VALIDATION so the challenger accepts as often as the rule does
    p_va = model.predict_proba(Xva)[:, 1]
    threshold = float(np.quantile(p_va, 1 - agree_va.mean()))

    champion = evaluate(agree_re, yre)  # rule-v0
    challenger = evaluate(model.predict_proba(Xre)[:, 1] >= threshold, yre)
    gate = {
        "accuracy_not_worse": challenger["accepted_accuracy"] >= champion["accepted_accuracy"],
        "coverage_within_2pts": challenger["coverage"] >= champion["coverage"] - 0.02,
        "false_accept_upper95_le_25pct": challenger["false_accept_upper95"] <= 0.25,
    }
    gate_passed = all(gate.values())

    mlflow.set_experiment(EXPERIMENT)
    with (
        mlflow.start_run(run_name=f"revalidate-{args.window}") as run,
        tempfile.TemporaryDirectory() as tmp,
    ):
        mlflow.log_params(
            {
                "window": args.window,
                "data_sha256": data_sha,
                "data_file": Path(args.data).name,
                "feature_version": FEATURE_VERSION,
                "features": ",".join(FEATURES),
                "model": "LogisticRegression(C=1.0)",
                "threshold": round(threshold, 4),
                "split_sizes": json.dumps({k: len(v) for k, v in by_split.items()}),
                "git_commit": os.environ.get("GIT_COMMIT", "unknown"),
            }
        )
        mlflow.log_metrics(
            {
                **{f"champion_{k}": v for k, v in champion.items()},
                **{f"challenger_{k}": v for k, v in challenger.items()},
                "gate_passed": float(gate_passed),
            }
        )
        mlflow.set_tags(
            {"gate_passed": str(gate_passed), "data_kind": rows[0].get("label_source", "?")}
        )
        # MLServer-ready package (what the policy server loads) as plain artifacts
        pkg = Path(tmp) / "mlserver_model"
        pkg.mkdir()
        joblib.dump(model, pkg / "model.joblib")
        (pkg / "model-settings.json").write_text(
            json.dumps(
                {
                    "name": "routing-policy",
                    "implementation": "mlserver_sklearn.SKLearnModel",
                    "parameters": {
                        "uri": "./model.joblib",
                        "version": f"run-{run.info.run_id[:8]}",
                    },
                },
                indent=2,
            )
        )
        (pkg / "thresholds.json").write_text(json.dumps({"balanced": threshold}))
        mlflow.log_artifacts(str(pkg), artifact_path="mlserver_model")
        info = mlflow.sklearn.log_model(model, name="policy", registered_model_name=MODEL_NAME)

    client = MlflowClient()
    version = info.registered_model_version
    client.set_registered_model_alias(MODEL_NAME, "challenger", version)
    client.set_model_version_tag(MODEL_NAME, version, "gate_passed", str(gate_passed))

    summary = {
        "window": args.window,
        "run_id": run.info.run_id,
        "model": MODEL_NAME,
        "version": version,
        "alias": "challenger",
        "data_sha256": data_sha,
        "threshold": threshold,
        "champion_rule_v0": champion,
        "challenger": challenger,
        "gate": gate,
        "gate_passed": gate_passed,
        "next_step": "eligible for promotion (human approval)"
        if gate_passed
        else "stay on champion",
    }
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / f"s2_revalidate_{args.window}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
