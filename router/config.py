"""Router settings, read once from environment variables (12-factor; values live in .env).

Every behaviour-changing value has a version string that is returned in each response and
written to every log row (ADR-17), so any answer can be traced to the exact configuration.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


@dataclass(frozen=True)
class Settings:
    """All configuration, read once at start-up from environment variables (`.env`);
    immutable."""

    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    api_key_sha256: str = field(default_factory=lambda: _env("ROUTER_API_KEY_SHA256"))
    hash_key: str = field(
        default_factory=lambda: _env("ROUTER_HASH_KEY")
    )  # HMAC key for prompt hashes
    # Admin key (interface I7: policy reload). Only its SHA-256 is stored; empty = admin disabled.
    # Created and rotated with `make admin-key`; must differ from the caller API key.
    admin_key_sha256: str = field(default_factory=lambda: _env("ROUTER_ADMIN_KEY_SHA256", ""))

    # Juniors: Ollama-compatible endpoints (Ollama on the Mac host, or the mock server in tests)
    junior1_url: str = field(
        default_factory=lambda: _env("JUNIOR1_URL", "http://host.docker.internal:11434")
    )
    junior1_model: str = field(default_factory=lambda: _env("JUNIOR1_MODEL", "junior-a"))
    junior2_url: str = field(
        default_factory=lambda: _env("JUNIOR2_URL", "http://host.docker.internal:11434")
    )
    junior2_model: str = field(default_factory=lambda: _env("JUNIOR2_MODEL", "junior-b"))
    junior_timeout_s: float = field(default_factory=lambda: float(_env("JUNIOR_TIMEOUT_S", "8")))
    junior_concurrency_cap: int = field(
        default_factory=lambda: int(_env("JUNIOR_CONCURRENCY_CAP", "4"))
    )

    # Circuit breakers (per provider, per router process)
    breaker_threshold: int = field(default_factory=lambda: int(_env("BREAKER_THRESHOLD", "5")))
    breaker_cooldown_s: float = field(
        default_factory=lambda: float(_env("BREAKER_COOLDOWN_S", "60"))
    )

    # Senior: reached ONLY through the egress gateway. Provider "mock" (tests, S1-S3) or
    # "anthropic" (Claude Messages API, interface I4).
    senior_provider: str = field(default_factory=lambda: _env("SENIOR_PROVIDER", "mock"))
    senior_api_version: str = field(
        default_factory=lambda: _env("SENIOR_API_VERSION", "2026-10-21")
    )
    senior_effort: str = field(default_factory=lambda: _env("SENIOR_EFFORT", "low"))
    senior_thinking_headroom: int = field(
        default_factory=lambda: int(_env("SENIOR_THINKING_HEADROOM", "1024"))
    )
    senior_url: str = field(
        default_factory=lambda: _env("SENIOR_URL", "http://mock-models:9100/senior/v1/chat")
    )
    senior_model: str = field(default_factory=lambda: _env("SENIOR_MODEL", "senior-mock"))
    senior_api_key: str = field(
        default_factory=lambda: _env("SENIOR_API_KEY", "not-needed-for-mock")
    )
    senior_timeout_s: float = field(default_factory=lambda: float(_env("SENIOR_TIMEOUT_S", "20")))
    senior_price_in_per_mtok: float = field(
        default_factory=lambda: float(_env("SENIOR_PRICE_IN", "10"))
    )
    senior_price_out_per_mtok: float = field(
        default_factory=lambda: float(_env("SENIOR_PRICE_OUT", "50"))
    )
    spend_cap_usd: float = field(default_factory=lambda: float(_env("SPEND_CAP_USD", "30")))

    # Routing policy served by MLServer (interface I5). Empty URL = rule-v0 only.
    policy_url: str = field(default_factory=lambda: _env("POLICY_URL", ""))
    policy_meta_file: str = field(
        default_factory=lambda: _env(
            "POLICY_META_FILE", "/policy-models/routing-policy/policy_meta.json"
        )
    )
    policy_timeout_s: float = field(default_factory=lambda: float(_env("POLICY_TIMEOUT_S", "0.06")))

    # Load the local models at start-up so the first request is not slow (0 = off)
    warmup: bool = field(default_factory=lambda: _env("WARMUP", "1") == "1")

    # Audit sampling (ADR-26): share of accepted, non-sensitive answers re-asked to the senior
    audit_rate: float = field(default_factory=lambda: float(_env("AUDIT_RATE", "0.05")))

    # Versions (ADR-17)
    api_version: str = "v1"
    policy_version: str = field(default_factory=lambda: _env("POLICY_VERSION", "rule-v0"))
    prompts_file: str = field(
        default_factory=lambda: _env("PROMPTS_FILE", "prompts/junior_prompts_v2.json")
    )

    def __post_init__(self) -> None:
        """Fail at start-up, not on the first paid call, when something is misconfigured."""
        if self.admin_key_sha256:
            if not re.fullmatch(r"[0-9a-f]{64}", self.admin_key_sha256):
                raise RuntimeError("ROUTER_ADMIN_KEY_SHA256 must be a SHA-256 hex digest")
            if self.admin_key_sha256 == self.api_key_sha256:
                raise RuntimeError("the admin key must differ from the caller API key")
        if self.senior_provider not in ("mock", "anthropic"):
            raise RuntimeError("SENIOR_PROVIDER must be 'mock' or 'anthropic'")
        if self.senior_provider == "anthropic":
            if not self.senior_url.startswith("https://"):
                raise RuntimeError("SENIOR_URL must be an https:// URL for the anthropic provider")
            if not self.senior_api_key.startswith("sk-ant-"):
                raise RuntimeError("SENIOR_API_KEY is not set to an Anthropic key")
