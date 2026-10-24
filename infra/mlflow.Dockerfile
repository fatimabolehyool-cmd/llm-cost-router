# MLflow tracking server + model registry, PostgreSQL backend, artifacts served over HTTP.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MLFLOW_DISABLE_TELEMETRY=true DO_NOT_TRACK=true
COPY infra/requirements-mlops.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock \
 && useradd --create-home --uid 1000 app && mkdir -p /mlartifacts && chown app /mlartifacts
USER app
EXPOSE 5000
# Note: a postgresql:// URL uses the psycopg (v3) driver with SQLAlchemy 2.1 (found 22 Oct).
CMD ["sh", "-c", "mlflow server --backend-store-uri postgresql://mlflow:${MLFLOW_DB_PASSWORD}@postgres:5432/mlflow --artifacts-destination /mlartifacts --serve-artifacts --host 0.0.0.0 --port 5000 --workers 1 --allowed-hosts 'mlflow,mlflow:5000,localhost,localhost:*,127.0.0.1,127.0.0.1:*'"]
