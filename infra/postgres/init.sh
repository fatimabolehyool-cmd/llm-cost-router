#!/bin/bash
# Runs once, on the first start of an empty data volume.
# One PostgreSQL instance, but a separate database AND user per tool, so a migration in one
# tool (MLflow, Airflow) can never touch the router's tables.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-SQL
  CREATE USER router  WITH PASSWORD '${ROUTER_DB_PASSWORD}';
  CREATE USER mlflow  WITH PASSWORD '${MLFLOW_DB_PASSWORD}';
  CREATE USER airflow WITH PASSWORD '${AIRFLOW_DB_PASSWORD}';
  CREATE DATABASE router  OWNER router;
  CREATE DATABASE mlflow  OWNER mlflow;
  CREATE DATABASE airflow OWNER airflow;
  REVOKE ALL ON DATABASE router  FROM PUBLIC;
  REVOKE ALL ON DATABASE mlflow  FROM PUBLIC;
  REVOKE ALL ON DATABASE airflow FROM PUBLIC;
SQL
