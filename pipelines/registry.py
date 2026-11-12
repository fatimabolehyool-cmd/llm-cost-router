"""Promotion and rollback with MLflow registry aliases (ADR-6). Always a deliberate human action.

python -m pipelines.registry show [--json]           # aliases and every version's gate result
python -m pipelines.registry promote --version 3     # champion -> v3, remembers the previous one
python -m pipelines.registry rollback                # champion -> the previous champion
python -m pipelines.registry export                  # copy the champion's package for serving
python -m pipelines.registry activate                # hot reload only (after fixing a failure)

promote and rollback export the new champion automatically, so what MLServer serves always
matches the registry. The serving folder is generated from MLflow and is never edited by hand.
With POLICY_SERVER_URL, ROUTER_URL and ROUTER_ADMIN_KEY set (the Airflow container has them),
they then switch MLServer and the router to it without a restart (pipelines/serving.py, I7).
Exit codes: 0 done, 2 refused (gate not passed / nothing to roll back), 3 exported but serving
did not switch (the previous model keeps serving; fix the cause and run `activate`).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from pipelines import serving

MODEL = "routing-policy"
ALIASES = ("champion", "challenger", "previous_champion")
SERVE_DIR = Path("policy_models")  # bind-mounted into MLServer (/models) and the router
MLSERVER_SETTINGS = {
    "http_port": 8080,
    "grpc_port": 8081,
    "metrics_port": 8082,
    "parallel_workers": 0,
}


def alias_version(c: MlflowClient, alias: str) -> str | None:
    """The version number an alias points to, as a string (MLflow returns an int), or None."""
    try:
        return str(c.get_model_version_by_alias(MODEL, alias).version)
    except MlflowException:
        return None


def export(c: MlflowClient, dest: Path = SERVE_DIR, alias: str = "champion") -> str | None:
    """Copy the aliased version's MLServer package (model.joblib, model-settings.json,
    policy_meta.json) to dest/routing-policy. Without a champion the folder is removed and the
    router falls back to rule-v0. The swap is a rename, so a reader never sees half a model."""
    target = dest / MODEL
    version = alias_version(c, alias)
    if version is None:
        if target.exists():
            shutil.rmtree(target)
        print(f"no {alias}: nothing to serve, the router uses rule-v0")
        return None
    run_id = c.get_model_version(MODEL, version).run_id
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        pkg = Path(
            mlflow.artifacts.download_artifacts(
                run_id=run_id, artifact_path="mlserver_model", dst_path=tmp
            )
        )
        meta = json.loads((pkg / "policy_meta.json").read_text())
        old = dest / f".{MODEL}.old"
        if old.exists():
            shutil.rmtree(old)
        if target.exists():
            target.rename(old)
        pkg.rename(target)
        if old.exists():
            shutil.rmtree(old)
    (dest / "settings.json").write_text(json.dumps(MLSERVER_SETTINGS, indent=2))
    print(
        f"exported version {version} ({meta['model_version']}, {meta['feature_version']}) to {target}"
    )
    return version


def describe(c: MlflowClient) -> dict:
    """Aliases plus every registered version with its gate result and serving version string."""
    versions = []
    for mv in sorted(c.search_model_versions(f"name='{MODEL}'"), key=lambda m: int(m.version)):
        params = c.get_run(mv.run_id).data.params
        versions.append(
            {
                "version": str(mv.version),
                "model_version": f"run-{mv.run_id[:8]}",
                "feature_version": params.get("feature_version", "?"),
                "gate_passed": mv.tags.get("gate_passed") == "True",
            }
        )
    return {"aliases": {a: alias_version(c, a) for a in ALIASES}, "versions": versions}


def switch_serving(champion: str | None) -> int:
    """Hot reload (I7) if configured. 0 = switched or not configured, 3 = did not switch."""
    cfg = serving.from_env()
    if cfg is None:
        print(
            "hot reload not configured (POLICY_SERVER_URL, ROUTER_URL, ROUTER_ADMIN_KEY): "
            "restart with `make real-up` to serve the exported model"
        )
        return 0
    try:
        body = serving.activate(*cfg, champion_present=champion is not None)
    except serving.ActivationError as e:
        print(f"serving did NOT switch: {e}")
        print("the previous model keeps serving; fix the cause, then run `make reload`")
        return 3
    print(f"serving {body.get('policy')} (was {body.get('previous')}), without a restart")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("--json", action="store_true")
    p = sub.add_parser("promote")
    p.add_argument("--version", required=True)
    for parser in (p, sub.add_parser("rollback"), sub.add_parser("export")):
        parser.add_argument("--no-reload", action="store_true", help="export only, no hot reload")
    sub.add_parser("activate")
    a = ap.parse_args()
    c = MlflowClient()

    if a.cmd == "show" and a.json:
        print(json.dumps(describe(c), indent=2))
        return 0
    if a.cmd == "activate":
        return switch_serving(alias_version(c, "champion"))

    if a.cmd == "promote":
        current = alias_version(c, "champion")
        if c.get_model_version(MODEL, a.version).tags.get("gate_passed") != "True":
            print(f"refused: version {a.version} did not pass the release gate")
            return 2
        if current == a.version:
            print(f"version {a.version} is already the champion")
            return 0
        if current:
            c.set_registered_model_alias(MODEL, "previous_champion", current)
        c.set_registered_model_alias(MODEL, "champion", a.version)
    elif a.cmd == "rollback":
        prev = alias_version(c, "previous_champion")
        if not prev:
            print(
                "nothing to roll back to: the champion falls back to rule-v0 if its alias is removed"
            )
            return 2
        c.set_registered_model_alias(MODEL, "champion", prev)
        c.delete_registered_model_alias(MODEL, "previous_champion")
    rc = 0
    if a.cmd in ("promote", "rollback", "export"):
        exported = export(c)
        if not a.no_reload:
            rc = switch_serving(exported)

    for alias in ALIASES:
        print(
            f"{alias:18s} -> {alias_version(c, alias) or '(none: rule-v0)' if alias == 'champion' else alias_version(c, alias) or '-'}"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
