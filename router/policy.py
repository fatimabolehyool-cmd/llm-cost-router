"""Routing policy: the client for the policy server (interface I5) and the model's metadata.

The policy predicts P(the juniors' answer is correct). In balanced mode the router accepts the
junior answer when P >= the model's threshold, otherwise it escalates (or abstains for sensitive
requests). Any problem with the policy (server down, timeout, 5xx, malformed reply, version
mismatch) raises PolicyUnavailable and the orchestrator falls back to rule-v0, the agreement
rule, in-process (ADR-2). A policy failure can therefore change cost, never safety.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import httpx

from common.policy_features import (
    FEATURE_VERSION,
    FEATURE_VERSION_V2,
    FEATURES,
    FEATURES_V2,
    answer_prior,
    build,
    build_v2,
)

SUPPORTED_FEATURES = {FEATURE_VERSION: FEATURES, FEATURE_VERSION_V2: FEATURES_V2}


class PolicyUnavailable(Exception):
    """The policy could not give a usable probability; the caller falls back to rule-v0."""


@dataclass(frozen=True)
class PolicyMeta:
    """Everything the router needs to use one trained model (policy_meta.json, written by
    pipelines/revalidate.py next to model.joblib)."""

    model_version: str
    feature_version: str
    features: tuple[str, ...]
    threshold_balanced: float
    answer_prior: dict | None

    @classmethod
    def load(cls, path: str | Path) -> PolicyMeta:
        m = json.loads(Path(path).read_text(encoding="utf-8"))
        version = m["feature_version"]
        if version not in SUPPORTED_FEATURES:
            raise ValueError(f"unsupported feature version {version!r}")
        if list(m["features"]) != SUPPORTED_FEATURES[version]:
            raise ValueError(f"feature list in {path} does not match the {version} contract")
        if version == FEATURE_VERSION_V2 and not m.get("answer_prior"):
            raise ValueError(f"{version} model without an answer_prior table")
        threshold = float(m["thresholds"]["balanced"])
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold outside [0, 1]")
        return cls(
            model_version=m["model_version"],
            feature_version=version,
            features=tuple(m["features"]),
            threshold_balanced=threshold,
            answer_prior=m.get("answer_prior"),
        )

    def vector(
        self,
        task_type: str,
        n_options: int,
        a1: str | None,
        a2: str | None,
        prompt_tokens: int,
        j1_tokens: int,
        j2_tokens: int,
    ) -> list[float]:
        """Build the feature vector exactly as the training pipeline did."""
        agree = a1 is not None and a1 == a2
        args = (
            task_type,
            n_options,
            agree,
            a1 is not None,
            a2 is not None,
            prompt_tokens,
            j1_tokens,
            j2_tokens,
        )
        if self.feature_version == FEATURE_VERSION:
            return build(*args)
        return build_v2(*args, answer_prior(self.answer_prior, task_type, a1, a2))


class PolicyClient:
    """V2 Inference Protocol client for MLServer (interface I5)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        meta: PolicyMeta,
        timeout_s: float = 0.06,
        model_name: str = "routing-policy",
    ) -> None:
        self.client, self.meta, self.timeout_s = client, meta, timeout_s
        self.endpoint = f"{url.rstrip('/')}/v2/models/{model_name}/infer"

    async def p_correct(self, x: list[float]) -> float:
        n = len(self.meta.features)
        # Validate before sending: MLServer answers malformed input with a 500 and then drops
        # the kept-alive connection (Experiment 2), so the router never sends a bad vector.
        if len(x) != n or not all(math.isfinite(v) for v in x):
            raise PolicyUnavailable("invalid_features")
        payload = {
            "inputs": [{"name": "features", "shape": [1, n], "datatype": "FP32", "data": x}],
            "outputs": [{"name": "predict_proba"}],
        }
        for attempt in (1, 2):
            try:
                r = await self.client.post(self.endpoint, json=payload, timeout=self.timeout_s)
                break
            except (httpx.RemoteProtocolError, httpx.ConnectError) as e:
                if attempt == 2:  # one retry on a dropped connection, then give up
                    raise PolicyUnavailable(type(e).__name__) from None
            except httpx.TimeoutException:
                raise PolicyUnavailable("timeout") from None
            except httpx.HTTPError as e:
                raise PolicyUnavailable(type(e).__name__) from None
        if r.status_code != 200:
            raise PolicyUnavailable(f"http_{r.status_code}")
        try:
            body = r.json()
            out = body["outputs"][0]
            served = body.get("model_version")
            data = [float(v) for v in out["data"]]
        except (ValueError, KeyError, IndexError, TypeError):
            raise PolicyUnavailable("malformed_reply") from None
        # The server must run the same model the router built features for (no silent skew).
        if served is not None and served != self.meta.model_version:
            raise PolicyUnavailable("model_version_mismatch")
        if out.get("name") != "predict_proba" or len(data) != 2:
            raise PolicyUnavailable("malformed_reply")
        p = data[1]
        if not 0.0 <= p <= 1.0:
            raise PolicyUnavailable("probability_out_of_range")
        return p
