"""S2 — Lifecycle tools: do Airflow and MLflow run on this Mac and work together?

Question: can Airflow (scheduler) run the re-validation pipeline, which trains a challenger,
logs it to MLflow (PostgreSQL backend), registers it with the `challenger` alias, and is the
release gate enforced when someone tries to promote it? How much memory does the full stack use?

Steps (all measured; written to results/s2_airflow_mlflow_summary.md):
  1. start the full stack (mocks + lifecycle + orchestration profiles); time until Airflow is healthy
  2. the DAG imports without errors
  3. `airflow dags test revalidate_policy` runs the pipeline inside Airflow
  4. MLflow registry shows a new version with alias `challenger`
  5. promoting a version that failed the gate is refused (exit code 2)
  6. docker stats memory for every container
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx
from harness import COMPOSE, RESULTS

PROFILES = ["--profile", "mocks", "--profile", "orchestration"]
MLFLOW = "http://localhost:5001"


def run(*args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)


def health(service: str) -> str:
    cid = run(*COMPOSE, *PROFILES, "ps", "-q", service).stdout.strip()
    return (
        run("docker", "inspect", "-f", "{{.State.Health.Status}}", cid).stdout.strip()
        if cid
        else "missing"
    )


def main() -> int:
    rows = []

    def check(name, observed, required, ok):
        rows.append((name, str(observed), required, bool(ok)))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {observed}")

    t0 = time.time()
    up = run(*COMPOSE, *PROFILES, "up", "-d", "--build")
    if up.returncode != 0:
        print(up.stderr[-3000:])
        return 1
    while health("airflow") != "healthy" and time.time() - t0 < 900:
        time.sleep(5)
    check(
        "Full stack built, started, Airflow healthy",
        f"{time.time() - t0:.0f} s",
        "< 900 s",
        health("airflow") == "healthy",
    )

    exec_af = [*COMPOSE, *PROFILES, "exec", "-T", "-w", "/opt/project", "airflow"]
    errs = run(*exec_af, "airflow", "dags", "list-import-errors")
    check(
        "DAG import errors",
        "none" if "No data found" in errs.stdout + errs.stderr else errs.stdout[-300:],
        "none",
        "No data found" in errs.stdout + errs.stderr,
    )

    t1 = time.time()
    test = run(*exec_af, "airflow", "dags", "test", "revalidate_policy", "2026-10-19")
    ok_run = test.returncode == 0 and "state=success" in (test.stdout + test.stderr)
    check(
        "Airflow ran the pipeline (dags test)",
        f"exit {test.returncode} in {time.time() - t1:.0f} s",
        "success",
        ok_run,
    )

    try:
        a = httpx.get(
            f"{MLFLOW}/api/2.0/mlflow/registered-models/alias",
            params={"name": "routing-policy", "alias": "challenger"},
            timeout=10,
        ).json()
        version = a["model_version"]["version"]
        gate = {t["key"]: t["value"] for t in a["model_version"].get("tags", [])}.get("gate_passed")
    except Exception as e:  # noqa: BLE001
        version, gate = None, f"error: {e}"
    check(
        "MLflow registry: challenger alias set",
        f"version {version}, gate_passed={gate}",
        "a version",
        version is not None,
    )

    if version and gate == "False":
        pr = run(
            *exec_af,
            "env",
            "MLFLOW_TRACKING_URI=http://mlflow:5000",
            "/opt/pipelines-venv/bin/python",
            "-m",
            "pipelines.registry",
            "promote",
            "--version",
            version,
        )
        check(
            "Promotion of a gate-failing version is refused",
            f"exit {pr.returncode}",
            "exit 2",
            pr.returncode == 2,
        )

    stats = run(
        "docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
    ).stdout
    images = run(*COMPOSE, *PROFILES, "images", "--format", "json").stdout
    summary_json = next((p for p in sorted(RESULTS.glob("revalidate_*.json"))), None)

    passed = all(ok for *_, ok in rows)
    lines = [
        f"# S2 Airflow + MLflow ({datetime.now(UTC).isoformat(timespec='seconds')})",
        "",
        f"**Result: {'PASS' if passed else 'FAIL'}.** All values measured on this machine. "
        "The replay data is SYNTHETIC: this proves the mechanics, not the policy's quality.",
        "",
        "| Check | Observed | Required | Result |",
        "|---|---|---|---|",
        *[f"| {a} | {b} | {c} | {'pass' if ok else 'FAIL'} |" for a, b, c, ok in rows],
        "",
        "## Memory and CPU, whole stack (docker stats)",
        "",
        "```",
        stats.strip(),
        "```",
        "",
        "## Images (for docs/versions.md)",
        "",
        "```",
        images.strip()[:4000],
        "```",
    ]
    if summary_json:
        lines += [
            "",
            f"## Pipeline output ({summary_json.name})",
            "",
            "```json",
            summary_json.read_text()[:3000],
            "```",
        ]
    if not ok_run:
        lines += [
            "",
            "## Airflow output (tail)",
            "",
            "```",
            (test.stdout + test.stderr)[-3000:],
            "```",
        ]
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / "s2_airflow_mlflow_summary.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"\nsaved {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
