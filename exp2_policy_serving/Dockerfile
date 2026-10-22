# One image for training AND serving the routing policy, so scikit-learn is identical
# in both (a model pickled with one sklearn version may not load in another).
# Python 3.11: MLServer 1.7.1 does not install on Python 3.13 (checked 2026-10-19).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /work

COPY exp2_policy_serving/requirements-serving.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

# Run as a non-root user.
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8080 8081 8082
CMD ["mlserver", "start", "/work/exp2_policy_serving/model_repo"]
