"""Router API (interface I1): POST /v1/answer, GET /health, GET /ready; POST /v1/feedback (I2);
admin (I7, separate admin key): GET /admin/policy, POST /admin/policy/reload;
demo page (FR-15): GET /demo, GET /demo/samples.

Run: uvicorn router.app:app --host 0.0.0.0 --port 8000
Logs never contain question text or model output: only request ID, outcome and timings.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, model_validator

from . import metrics
from .adapters import AnthropicSenior, CircuitBreaker, HttpSenior, OllamaJunior
from .audit import AuditSampler
from .config import Settings
from .egress import EgressGateway
from .orchestrator import JuniorsUnavailable, Orchestrator, Overloaded
from .policy import PolicyClient, PolicyMeta, PolicyUnavailable
from .privacy import mask
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("router")


class AnswerRequest(BaseModel):
    """Body of POST /v1/answer (interface I1). task_type may be omitted: exactly 4 options
    are read as a choice question, anything else as a label task."""

    question: str = Field(min_length=1, max_length=4000)
    # Optional (I1 v1.1): when omitted it is inferred from the options, exactly 4 -> "choice",
    # otherwise "label" (a customer message sorted into the caller's request types).
    task_type: Literal["choice", "label"] | None = None
    options: list[str] = Field(min_length=2, max_length=100)
    mode: Literal["cheapest", "balanced", "best_quality"] = "balanced"
    sensitive: bool = False

    @model_validator(mode="after")
    def check(self) -> AnswerRequest:
        """Cross-field checks: infer task_type; a choice needs exactly 4 options; options
        must be unique and 1-200 characters; the question must be mostly English (otherwise 400)."""
        if self.task_type is None:
            self.task_type = "choice" if len(self.options) == 4 else "label"
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
    """The routing record returned for every answered or abstained request."""

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
    task_type: str | None = None  # additive v1.1 field: given or inferred task type


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


class FeedbackRequest(BaseModel):
    """Interface I2: a caller's verdict on one routing record."""

    request_id: str = Field(pattern=r"^req_[0-9a-f]{12}$")
    flag: Literal["wrong", "correct", "unsure"]
    note: str | None = Field(default=None, max_length=500)


class FeedbackResponse(BaseModel):
    """Reply of POST /v1/feedback (interface I2)."""

    received: bool


async def warm_up(*juniors) -> None:
    """Load the local models into memory at start-up (the adapter asks Ollama to keep them for
    30 minutes), so the first real request does not wait for a model load (~6 s measured).
    Runs in the background; a failure is only logged and requests decide as usual."""
    t0 = time.perf_counter()
    results = await asyncio.gather(
        *(j.ask("Reply with one letter.", "A", 1) for j in juniors), return_exceptions=True
    )
    ready = sum(not isinstance(r, BaseException) for r in results)
    log.info(
        "warm-up: %d/%d local models ready in %.1f s", ready, len(juniors), time.perf_counter() - t0
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the services once at start-up and close them at shutdown.

    Start-up: HTTP client, store and migrations, juniors, senior adapter, egress gateway, policy
    client, audit sampler, orchestrator, metrics and the junior warm-up. Shutdown drains the
    audits and flushes the request log."""
    s = Settings()
    client = httpx.AsyncClient(limits=httpx.Limits(max_connections=50))
    store = Store(s.database_url, s.spend_cap_usd)
    await store.start()

    def cb() -> CircuitBreaker:
        """A new circuit breaker with the configured threshold and cooldown."""
        return CircuitBreaker(s.breaker_threshold, s.breaker_cooldown_s)

    j1 = OllamaJunior(client, s.junior1_url, s.junior1_model, s.junior_timeout_s, cb())
    j2 = OllamaJunior(client, s.junior2_url, s.junior2_model, s.junior_timeout_s, cb())
    senior = build_senior(s, client, cb())
    gateway = EgressGateway(senior, store, s.senior_price_in_per_mtok, s.senior_price_out_per_mtok)
    app.state.settings, app.state.store, app.state.gateway = s, store, gateway
    app.state.http, app.state.reload_lock = client, asyncio.Lock()
    audit = AuditSampler(gateway, store, rate=s.audit_rate) if s.audit_rate > 0 else None
    app.state.orch = Orchestrator(
        s, j1, j2, gateway, store, policy=build_policy(s, client), audit=audit
    )
    collector = metrics.RouterCollector(app.state.orch, gateway, store)
    metrics.REGISTRY.register(collector)
    metrics.SPEND_CAP.set(s.spend_cap_usd)
    metrics.initialize_series()
    warm = asyncio.create_task(warm_up(j1, j2)) if s.warmup else None
    log.info("router ready versions=%s", app.state.orch.versions)
    yield
    if warm is not None:
        warm.cancel()
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
    """Liveness: the process is up (no dependency is checked)."""
    return {"status": "ok"}  # process is up


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint (interface I10): counts and timings only, never content."""
    return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.get("/ready")
async def ready(request: Request):
    """Readiness: 200 when PostgreSQL answers, otherwise 503 not_ready."""
    ok = await request.app.state.store.ping()
    return {"status": "ready"} if ok else _error(503, "not_ready", "database unreachable")


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Runs before body validation, so unauthenticated callers learn nothing about the schema."""
    given = hashlib.sha256((x_api_key or "").encode()).hexdigest()
    if not hmac.compare_digest(given, request.app.state.settings.api_key_sha256):
        raise HTTPException(status_code=401, detail="unauthorized")


ADMIN_MESSAGES = {
    401: "missing or invalid admin key",
    403: "admin endpoints are disabled: set ROUTER_ADMIN_KEY_SHA256 (make admin-key)",
}


@app.exception_handler(HTTPException)
async def _http(request: Request, exc: HTTPException) -> JSONResponse:
    if request.url.path.startswith("/admin/"):
        message = ADMIN_MESSAGES.get(exc.status_code, "")
    else:
        message = "missing or invalid API key" if exc.status_code == 401 else ""
    return _error(exc.status_code, str(exc.detail), message)


@app.post("/v1/answer", response_model=AnswerResponse, dependencies=[Depends(require_api_key)])
async def answer(body: AnswerRequest, request: Request):
    """Interface I1: route one question and return its routing record.

    503 database_unavailable comes before any model call, because the spend cap cannot be
    enforced; 503 overloaded and models_unavailable come from the juniors, before any paid call.
    An abstention is a normal 200."""
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
    return AnswerResponse(**out.__dict__, task_type=body.task_type)


@app.post("/v1/feedback", response_model=FeedbackResponse, dependencies=[Depends(require_api_key)])
async def feedback(body: FeedbackRequest, request: Request, x_api_key: str | None = Header(None)):
    """Interface I2. Upsert per (request, caller): sending again replaces the earlier flag.
    Errors: 400 invalid body, 401 bad key, 404 unknown request_id (or its record is not written
    yet; retry after a second), 503 database unavailable. The note is masked before storage."""
    key_id = hashlib.sha256((x_api_key or "").encode()).hexdigest()[:12]
    note = mask(body.note) if body.note else None
    try:
        found = await request.app.state.store.upsert_feedback(
            body.request_id, key_id, body.flag, note
        )
    except Exception:  # noqa: BLE001 - database down or slow
        return _error(503, "database_unavailable", "feedback could not be stored; try later")
    if not found:
        return _error(404, "unknown_request", "no routing record with this request_id")
    metrics.FEEDBACK.labels(body.flag).inc()
    return FeedbackResponse(received=True)


# ---------------------------------------------------------------- admin (interface I7)


async def require_admin_key(
    request: Request, x_admin_key: str | None = Header(default=None)
) -> None:
    """Admin calls need X-Admin-Key; the caller API key is never accepted here."""
    expected = request.app.state.settings.admin_key_sha256
    if not expected:
        raise HTTPException(status_code=403, detail="admin_disabled")
    given = hashlib.sha256((x_admin_key or "").encode()).hexdigest()
    if not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _policy_state(orch) -> dict:
    p = orch.policy
    return {
        "policy": orch.versions["policy"],
        "feature_version": p.meta.feature_version if p else None,
        "threshold_balanced": p.meta.threshold_balanced if p else None,
    }


@app.get("/admin/policy", dependencies=[Depends(require_admin_key)])
async def admin_policy(request: Request) -> dict:
    """Which routing policy is serving now."""
    return _policy_state(request.app.state.orch)


PROBE_TIMEOUT_S = 2.0  # the first inference after a model load can be slower than the 60 ms


@app.post("/admin/policy/reload", dependencies=[Depends(require_admin_key)])
async def admin_policy_reload(request: Request):
    """Switch to the exported champion without restarting (interface I7).

    Called by `pipelines.registry promote|rollback` after MLServer has loaded the new model.
    The new metadata is read and a probe request must come back from the policy server with
    the same model_version, so the router never builds features for a model the server is not
    running. On any failure the current policy keeps serving: 409 reload_failed. No exported
    champion means rule-v0 decides. Reloads are serialised; requests are never paused.
    """
    st = request.app.state
    s = st.settings
    if not s.policy_url:
        return _error(409, "policy_not_configured", "POLICY_URL is not set; rule-v0 decides")
    async with st.reload_lock:
        previous = st.orch.versions["policy"]
        if not Path(s.policy_meta_file).exists():
            current = st.orch.set_policy(None)
            metrics.POLICY_RELOADS.labels("ok").inc()
            log.warning("policy reload: no champion exported; %s -> %s", previous, current)
            return {"reloaded": True, "previous": previous, **_policy_state(st.orch)}
        try:
            meta = PolicyMeta.load(s.policy_meta_file)
            probe = PolicyClient(st.http, s.policy_url, meta, timeout_s=PROBE_TIMEOUT_S)
            await probe.p_correct(meta.vector("choice", 4, "A", "A", 100, 2, 2))
        except (OSError, ValueError, KeyError, PolicyUnavailable) as e:
            reason = str(e) or type(e).__name__
            metrics.POLICY_RELOADS.labels("failed").inc()
            log.warning("policy reload refused (%s); %s keeps serving", reason, previous)
            return _error(409, "reload_failed", f"{reason}; {previous} keeps serving")
        current = st.orch.set_policy(
            PolicyClient(st.http, s.policy_url, meta, timeout_s=s.policy_timeout_s)
        )
    metrics.POLICY_RELOADS.labels("ok").inc()
    log.info("policy reloaded: %s -> %s", previous, current)
    return {"reloaded": True, "previous": previous, **_policy_state(st.orch)}


# ---------------------------------------------------------------- demo page (FR-15)

STATIC = Path(__file__).parent / "static"
# One self-contained page: inline script and style, and it may only call this router.
DEMO_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@app.get("/demo", include_in_schema=False)
async def demo_page() -> FileResponse:
    """Static page that calls /v1/answer with the API key typed into it (never embedded)."""
    return FileResponse(STATIC / "demo.html", media_type="text/html", headers=DEMO_HEADERS)


@app.get("/demo/samples", include_in_schema=False)
async def demo_samples() -> FileResponse:
    """40 labelled test-split questions for the replay button (pipelines/make_demo_samples.py)."""
    return FileResponse(
        STATIC / "demo_samples.json", media_type="application/json", headers=DEMO_HEADERS
    )
