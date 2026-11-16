# ADR 0005: Contribution, Quality Gate, and Explicit Release Model

- **Status**: Accepted (Target Policy — Legacy GitFlow Hooks Remain Active Until Cutover)
- **Date**: 2026-11-15

## Context

On the legacy `develop` branch, `.githooks/post-merge` and `.github/workflows/cicd.yml` couple branch integration directly to `scripts/deploy.sh`, which runs production/QA container rebuilds and swallows failed health checks with `|| true`. This creates unnecessary coupling between ordinary development commits and home-server operations.

## Decision (Target Policy for Rebuilt Vertex)

When Phase 6 cutover promotes the rebuilt application as primary:
1. Transition from GitFlow (`main` + `develop` auto-deploy hooks) to **one protected `main` branch + short-lived feature branches + pull requests + tagged releases**.
2. Remove auto-deployment from `.githooks/post-merge` and untrusted PR CI workflows; CI runs offline `unittest` suites, OpenAPI/frontend builds, contract checks, and container image builds only.
3. Release installation is an **explicit operator action**: verify a complete whole-instance backup exists, apply SQLite migrations explicitly (`python -m vertex migrate`), start the pinned image digest, block on `/healthz` and `/readyz`, and **fail fatally** on unhealthy startup (never `|| true`).
4. **Transition Guard**: While `src/vertex/` coexists beside the legacy application during Phases 0–5 (`feature/self-hosted-rebuild` and subsequent slice branches), existing `AGENTS.md`, `.githooks/`, `scripts/deploy.sh`, and `tests/test_cicd.py` remain unchanged so existing repository checks stay green. The cutover PR updates `AGENTS.md`, hooks, scripts, and CI together.

## Alternatives Considered

- **Immediate modification of `.githooks/post-merge` and `scripts/deploy.sh` during Phase 1**: Rejected because `tests/test_cicd.py` enforces exact contracts on legacy GitFlow deployment scripts until Phase 6 cutover; breaking legacy CI mid-rebuild blocks `scripts/check.sh`.
- **Continue auto-deploying via `post-merge` hooks post-cutover**: Rejected because ordinary code integration should not mutate running containers or ignore failed health checks (`|| true`).

## Revisit Triggers

- Execute GitFlow-to-trunk-based switch (`main` + PRs + tag-driven explicit release) during the Phase 6 cutover PR after explicit user approval.
