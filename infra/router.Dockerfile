# Router API and the mock model server share this image (different commands in Compose).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY infra/requirements-router.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY common/ common/
COPY router/ router/
COPY prompts/ prompts/
COPY mock_models/ mock_models/

RUN useradd --create-home --uid 1000 app && mkdir -p /records && chown app /records
USER app

EXPOSE 8000
CMD ["uvicorn", "router.app:app", "--host", "0.0.0.0", "--port", "8000"]
