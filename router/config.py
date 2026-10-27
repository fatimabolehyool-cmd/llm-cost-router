"""Router settings, read once from environment variables (12-factor; values live in .env).

Every behaviour-changing value has a version string that is returned in each response and
written to every log row (ADR-17), so any answer can be traced to the exact configuration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    api_key_sha256: str = field(default_factory=lambda: _env("ROUTER_API_KEY_SHA256"))
    hash_key: str = field(default_factory=lambda: _env("ROUTER_HASH_KEY"))  # HMAC key for prompt hashes

    # Juniors: Ollama-compatible endpoints (Ollama on the Mac host, or the mock server in tests)
    junior1_url: str = field(default_factory=lambda: _env("JUNIOR1_URL", "http://host.docker.internal:11434"))
    junior1_model: str = field(default_factory=lambda: _env("JUNIOR1_MODEL", "junior-a"))
    junior2_url: str = field(default_factory=lambda: _env("JUNIOR2_URL", "http://host.docker.internal:11434"))
    junior2_model: str = field(default_factory=lambda: _env("JUNIOR2_MODEL", "junior-b"))
    junior_timeout_s: float = field(default_factory=lambda: float(_env("JUNIOR_TIMEOUT_S", "8")))
    junior_concurrency_cap: int = field(default_factory=lambda: int(_env("JUNIOR_CONCURRENCY_CAP", "4")))

    # Circuit breakers (per provider, per router process)
    breaker_threshold: int = field(default_factory=lambda: int(_env("BREAKER_THRESHOLD", "5")))
    breaker_cooldown_s: float = field(default_factory=lambda: float(_env("BREAKER_COOLDOWN_S", "60")))

    # Senior: reached ONLY through the egress gateway
    senior_url: str = field(default_factory=lambda: _env("SENIOR_URL", "http://mock-models:9100/senior/v1/chat"))
    senior_model: str = field(default_factory=lambda: _env("SENIOR_MODEL", "senior-mock"))
    senior_api_key: str = field(default_factory=lambda: _env("SENIOR_API_KEY", "not-needed-for-mock"))
    senior_timeout_s: float = field(default_factory=lambda: float(_env("SENIOR_TIMEOUT_S", "20")))
    senior_price_in_per_mtok: float = field(default_factory=lambda: float(_env("SENIOR_PRICE_IN", "10")))
    senior_price_out_per_mtok: float = field(default_factory=lambda: float(_env("SENIOR_PRICE_OUT", "50")))
    spend_cap_usd: float = field(default_factory=lambda: float(_env("SPEND_CAP_USD", "30")))

    # Versions (ADR-17)
    api_version: str = "v1"
    policy_version: str = field(default_factory=lambda: _env("POLICY_VERSION", "rule-v0"))
    prompts_file: str = field(default_factory=lambda: _env("PROMPTS_FILE", "prompts/junior_prompts_v2.json"))
