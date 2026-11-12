"""Router API (interface I1): POST /v1/answer, GET /health, GET /ready.

Run: uvicorn router.app:app --host 0.0.0.0 --port 8000
Logs never contain question text or model output: only request ID, outcome and timings.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, model_validator

from . import metrics
from .adapters import AnthropicSenior, CircuitBreaker, HttpSenior, OllamaJunior
from .audit import AuditSampler
from .config import Settings
from .egress import EgressGateway
from .orchestrator import JuniorsUnavailable, Orchestrator, Overloaded
from .policy import PolicyClient, PolicyMeta
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("router")


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    task_type: Literal["choice", "label"]
    options: list[str] = Field(min_length=2, max_length=100)
    mode: Literal["cheapest", "balanced", "best_quality"] = "balanced"
    sensitive: bool = False

    @model_validator(mode="after")
    def check(self) -> AnswerRequest:
        if self.task_type == "choice" and len(self.options) != 4:
            raise ValueError("choice questions need exactly 4 options")
        if any(not o.strip() or len(o) > 200 for o in self.options):
            raise ValueError("each option must be 1-200 characters")
        if len(set(o.strip().lower() for o in self.options)) != len(self.options):
            raise ValueError("options must be unique")
        letters = [c for c in self.question if c.isalpha()]
        if letters and sum(c.isascii() for c in letters) / len(letters) < 0.9:
            raise ValueError("unsupported_language: only English is supported")  # ADR-21
        return self


class AnswerResponse(BaseModel):
    request_id: str
    status: Literal["answered", "abstained"]
    answer: str | None
    verified: bool
    escalated: bool
    model: str | None
    reason: str
    data_class: str
    cost_usd: float
    latency_ms: int
    versions: dict
    policy_p: float | None = None  # additive v1 field: the policy's confidence, when it decided


def build_senior(
    s: Settings, client: httpx.AsyncClient, breaker: CircuitBreaker
) -> HttpSenior | AnthropicSenior:
    """Pick the senior adapter. The real API key is only ever given to the real provider:
    the mock receives a placeholder, so a test run can never send the key anywhere."""
    if s.senior_provider == "anthropic":
        return AnthropicSenior(
            client,
            s.senior_url,
            s.senior_model,
            s.senior_api_key,
            s.senior_timeout_s,
            api_version=s.senior_api_version,
            effort=s.senior_effort or None,
            thinking_headroom=s.senior_thinking_headroom,
            breaker=breaker,
        )
    return HttpSenior(
        client, s.senior_url, s.senior_model, "mock-no-key", s.senior_timeout_s, breaker
    )


def build_policy(s: Settings, client: httpx.AsyncClient) -> PolicyClient | None:
    """The trained policy if one is configured and its metadata is valid; otherwise None, and
    the router runs rule-v0. A missing or broken model never stops the router from starting."""
    if not s.policy_url:
        log.info("policy: none configured, rule-v0 decides")
        return None
    try:
        meta = PolicyMeta.load(s.policy_meta_file)
    except (OSError, ValueError, KeyError) as e:
        log.warning("policy: cannot use %s (%s); rule-v0 decides", s.policy_meta_file, e)
        return None
    log.info("policy: %s (%s)", meta.model_version, meta.feature_version)
    return PolicyClient(client, s.policy_url, meta, timeout_s=s.policy_timeout_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = Settings()
    client = httpx.AsyncClient(limits=httpx.Limits(max_connections=50))
    store = Store(s.database_url, s.spend_cap_usd)
    await store.start()

    def cb() -> CircuitBreaker:
        return CircuitBreaker(s.breaker_threshold, s.breaker_cooldown_s)

    j1 = OllamaJunior(client, s.junior1_url, s.junior1_model, s.junior_timeout_s, cb())
    j2 = OllamaJunior(client, s.junior2_url, s.junior2_model, s.junior_timeout_s, cb())
    senior = build_senior(s, client, cb())
    gateway = EgressGateway(senior, store, s.senior_price_in_per_mtok, s.senior_price_out_per_mtok)
    app.state.settings, app.state.store, app.state.gateway = s, store, gateway
    audit = AuditSampler(gateway, store, rate=s.audit_rate) if s.audit_rate > 0 else None
    app.state.orch = Orchestrator(
        s, j1, j2, gateway, store, policy=build_policy(s, client), audit=audit
    )
    collector = metrics.RouterCollector(app.state.orch, gateway, store)
    metrics.REGISTRY.register(collector)
    metrics.SPEND_CAP.set(s.spend_cap_usd)
    log.info("router ready versions=%s", app.state.orch.versions)
    yield
    if audit is not None:
        await audit.drain()
    metrics.REGISTRY.unregister(collector)
    await store.stop()
    await client.aclose()


app = FastAPI(title="Cost-aware LLM router", version="0.1.0", lifespan=lifespan)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
    # 400 (not FastAPI's default 422) to match the contract; never echo the input back.
    msgs = "; ".join(str(e.get("msg", "invalid")) for e in exc.errors())
    return _error(400, "invalid_request", msgs[:300])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}  # process is up


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint (interface I10): counts and timings only, never content."""
    return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.get("/ready")
async def ready(request: Request):
    ok = await request.app.state.store.ping()
    return {"status": "ready"} if ok else _error(503, "not_ready", "database unreachable")


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Runs before body validation, so unauthenticated callers learn nothing about the schema."""
    given = hashlib.sha256((x_api_key or "").encode()).hexdigest()
    if not hmac.compare_digest(given, request.app.state.settings.api_key_sha256):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.exception_handler(HTTPException)
async def _http(_: Request, exc: HTTPException) -> JSONResponse:
    return _error(
        exc.status_code,
        str(exc.detail),
        "missing or invalid API key" if exc.status_code == 401 else "",
    )


@app.post("/v1/answer", response_model=AnswerResponse, dependencies=[Depends(require_api_key)])
async def answer(body: AnswerRequest, request: Request):
    if not await request.app.state.store.ping():
        metrics.REJECTED.labels("database_unavailable").inc()
        return _error(503, "database_unavailable", "spend cap cannot be enforced; try later")
    try:
        out = await request.app.state.orch.answer(
            body.question, body.task_type, body.options, body.mode, body.sensitive
        )
    except Overloaded:
        metrics.REJECTED.labels("overloaded").inc()
        return _error(503, "overloaded", "junior capacity full; retry later")
    except JuniorsUnavailable:
        metrics.REJECTED.labels("models_unavailable").inc()
        return _error(503, "models_unavailable", "local models unavailable")
    metrics.record_outcome(out, body.mode, out.policy_p)
    log.info(
        "request_id=%s status=%s reason=%s class=%s escalated=%s latency_ms=%s",
        out.request_id,
        out.status,
        out.reason,
        out.data_class,
        out.escalated,
        out.latency_ms,
    )
    return AnswerResponse(**out.__dict__)
