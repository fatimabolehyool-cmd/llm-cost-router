"""Airflow DAG: weekly policy re-validation (ADR-15, ADR-22).

The DAG only SCHEDULES; all logic lives in the pipelines CLI, which runs in its own virtual
environment (/opt/pipelines-venv) so the pipeline's libraries (MLflow, scikit-learn) never
conflict with Airflow's. The same command runs without Airflow, which keeps it testable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param

PROJECT = os.environ.get("PROJECT_DIR", "/opt/project")
PYTHON = os.environ.get("PIPELINES_PYTHON", "/opt/pipelines-venv/bin/python")
MLFLOW = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
PIPELINE = f"cd {PROJECT} && {PYTHON} -m pipelines.revalidate"
SHADOW = f"cd {PROJECT} && {PYTHON} -m pipelines.shadow"
ENV = {
    "MLFLOW_TRACKING_URI": MLFLOW,
    "MLFLOW_DISABLE_TELEMETRY": "true",
    "MLFLOW_DISABLE_AGENT_HINT": "1",
    "DO_NOT_TRACK": "true",
}

with DAG(
    dag_id="revalidate_policy",
    description="Re-validate the routing policy on a replayed weekly window and register a challenger",
    schedule="@weekly",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    tags=["router", "mlops"],
    params={
        # Real replay pool by default; the synthetic fixture is kept for pipeline smoke tests.
        "replay_file": Param("data/replay/pool_v1.jsonl", type="string"),
        "features": Param("v2", enum=["v1", "v2"]),
    },
) as dag:
    revalidate = BashOperator(
        task_id="revalidate",
        bash_command=(
            f"{PIPELINE} --data {PROJECT}/{{{{ params.replay_file }}}} "
            f"--features {{{{ params.features }}}} --window {{{{ ds }}}} --out {PROJECT}/results"
        ),
        env=ENV,
        append_env=True,
    )
    # Offline shadow: the new challenger against the champion serving now, request by request
    # (the gate above only compares with rule-v0). The report is attached to the MLflow run.
    shadow = BashOperator(
        task_id="shadow_report",
        bash_command=(
            f"{SHADOW} --data {PROJECT}/{{{{ params.replay_file }}}} --out {PROJECT}/results"
        ),
        env=ENV,
        append_env=True,
    )
    revalidate >> shadow
