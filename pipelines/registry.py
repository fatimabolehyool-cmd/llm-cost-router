"""Promotion and rollback with MLflow registry aliases (ADR-6). Always a deliberate human action.

  python -m pipelines.registry show
  python -m pipelines.registry promote --version 3     # champion -> v3, remembers the previous one
  python -m pipelines.registry rollback                # champion -> the previous champion
"""
from __future__ import annotations

import argparse
import sys

from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

MODEL = "routing-policy"


def alias_version(c: MlflowClient, alias: str) -> str | None:
    try:
        return c.get_model_version_by_alias(MODEL, alias).version
    except MlflowException:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p = sub.add_parser("promote")
    p.add_argument("--version", required=True)
    sub.add_parser("rollback")
    a = ap.parse_args()
    c = MlflowClient()

    if a.cmd == "promote":
        current = alias_version(c, "champion")
        if c.get_model_version(MODEL, a.version).tags.get("gate_passed") != "True":
            print(f"refused: version {a.version} did not pass the release gate")
            return 2
        if current:
            c.set_registered_model_alias(MODEL, "previous_champion", current)
        c.set_registered_model_alias(MODEL, "champion", a.version)
    elif a.cmd == "rollback":
        prev = alias_version(c, "previous_champion")
        if not prev:
            print("nothing to roll back to: the champion falls back to rule-v0 if its alias is removed")
            return 2
        c.set_registered_model_alias(MODEL, "champion", prev)
        c.delete_registered_model_alias(MODEL, "previous_champion")

    for alias in ("champion", "challenger", "previous_champion"):
        print(f"{alias:18s} -> {alias_version(c, alias) or '(none: rule-v0)' if alias == 'champion' else alias_version(c, alias) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
