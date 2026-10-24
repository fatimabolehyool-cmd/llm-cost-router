# Airflow schedules; the pipeline runs in its OWN virtual environment so its libraries
# (MLflow, scikit-learn, SQLAlchemy 2.1) never conflict with Airflow's (ADR-15).
FROM apache/airflow:3.3.2-python3.12

USER root
RUN python -m venv /opt/pipelines-venv && chown -R airflow: /opt/pipelines-venv
USER airflow
COPY infra/requirements-mlops.lock /tmp/requirements-mlops.lock
RUN /opt/pipelines-venv/bin/pip install --no-cache-dir -r /tmp/requirements-mlops.lock
