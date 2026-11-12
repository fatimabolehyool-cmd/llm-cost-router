"""Airflow DAG: daily data retention (architecture section E, feedback A5).

Clears stored request text and feedback notes after 30 days and deletes routing records after
180 days. All logic is in pipelines/retention.py, so the same command runs without Airflow.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

PROJECT = os.environ.get("PROJECT_DIR", "/opt/project")
PYTHON = os.environ.get("PIPELINES_PYTHON", "/opt/pipelines-venv/bin/python")

with DAG(
    dag_id="data_retention",
    description="Clear request text after 30 days, delete routing records after 180 days",
    schedule="0 3 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["router", "data"],
) as dag:
    BashOperator(
        task_id="apply_retention",
        bash_command=(
            f"cd {PROJECT} && {PYTHON} -m pipelines.retention --text-days 30 --row-days 180"
        ),
        append_env=True,  # ROUTER_DATABASE_URL comes from the container environment
    )
