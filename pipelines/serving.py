"""Switch the running system to the exported champion without a restart (interface I7).

`pipelines.registry promote|rollback` export the new champion to policy_models/ and then call
activate(), which makes two requests:

  1. MLServer re-reads its model repository: POST /v2/repository/models/routing-policy/load.
     MLServer loads the new version before unloading the old one, so it keeps answering.
  2. The router re-reads policy_meta.json: POST /admin/policy/reload with the admin key. It sends
     a probe and switches only if the policy server answers with the same model_version.

Between the two steps the router's version check refuses the new server's answers, so those few
requests are decided by rule-v0: a switch can change cost for a moment, never safety. Standard
library only, so it runs in any environment and is tested without MLflow.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable

MODEL = "routing-policy"
ENV = ("POLICY_SERVER_URL", "ROUTER_URL", "ROUTER_ADMIN_KEY")


class ActivationError(RuntimeError):
    """Serving did not switch; the previous model keeps serving."""


Post = Callable[..., tuple[int, dict]]


def post(url: str, headers: dict | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    """POST with an empty body; returns (status, JSON body or {}). Network errors raise."""
    req = urllib.request.Request(url, data=b"", method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed URLs
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ActivationError(f"{url} unreachable ({getattr(e, 'reason', e)})") from None
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {"detail": raw[:200].decode(errors="replace")}
    return status, body if isinstance(body, dict) else {"detail": body}


def activate(
    policy_server_url: str,
    router_url: str,
    admin_key: str,
    champion_present: bool = True,
    send: Post = post,
) -> dict:
    """Load the exported model in MLServer, then switch the router. Returns the router's reply
    ({"previous", "policy", ...}); raises ActivationError if either step fails."""
    repo = f"{policy_server_url.rstrip('/')}/v2/repository/models/{MODEL}"
    if champion_present:
        status, body = send(f"{repo}/load")
        if status != 200:
            raise ActivationError(f"policy server did not load the model: HTTP {status} {body}")
    else:
        send(f"{repo}/unload")  # nothing to serve; the router moves to rule-v0 either way
    status, body = send(
        f"{router_url.rstrip('/')}/admin/policy/reload", headers={"X-Admin-Key": admin_key}
    )
    if status != 200:
        err = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
        raise ActivationError(
            f"router refused the reload: HTTP {status} {err.get('code', '')} {err.get('message', body)}"
        )
    return body


def from_env() -> tuple[str, str, str] | None:
    """(policy server URL, router URL, admin key) if all three are set, else None."""
    values = tuple(os.environ.get(name, "") for name in ENV)
    return values if all(values) else None  # type: ignore[return-value]
