# ADR-16: Prometheus and Grafana; alert rules in Grafana

**Status:** Accepted, versions changed. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Prometheus and Grafana monitor the router and the policy server; alert rules are shown in Grafana.

## Alternatives considered

Alertmanager e-mail; hosted monitoring.

## Reason and evidence

Free and local; one less service.

## As built

Pinned when monitoring was built: Prometheus 3.5.0 (LTS line) and Grafana 12.1.0, instead of the planned 3.13.5 and 13.2.4. Seven alert rules, each with an action; the dashboard is provisioned from JSON.
