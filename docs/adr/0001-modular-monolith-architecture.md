# ADR 0001: Modular Monolith Architecture

- **Status**: Accepted
- **Date**: 2026-11-15

## Context

The legacy application concentrates HTTP routing, auth middleware, schedulers, SQLite mutations, reconciliation, analytics, wishlist pricing, and MCP startup into `app.py` (~3,285 LOC) and `db.py` (~5,837 LOC), paired with `static/app.js` (~8,286 LOC). Understanding, testing, or modifying one feature risks regressions across unrelated domains.

## Decision

Rebuild Vertex under `src/vertex/` as a **modular monolith**: one codebase, one versioned container image, and one default Compose service with an in-process background runner backed by durable SQLite jobs (`jobs` table).

Inside `src/vertex/`, feature modules (`identity`, `catalog`, `library`, `activity`, future `wishlist`/`analytics`) organize cleanly into `models.py`, `repository.py`, and `service.py`. Domain services never import FastAPI, HTTP clients, or browser code. Cross-feature calls flow through explicit service contracts, never direct queries against another module's private tables. HTTP routers (`interfaces/http/`), CLI commands (`interfaces/cli.py`), scheduled jobs, and future MCP adapters invoke those same application services.

For the browser UI under `frontend/`, use TypeScript ES modules organized by feature (`shared/` + `features/{auth,library,settings,admin}/`) with OpenAPI-generated types and a build step compiling static assets into `frontend/dist/` for Python serving. Do not require Node.js in the home-server runtime container.

## Alternatives Considered

- **Split into separate Python microservices + Redis/Celery**: Excessive operational complexity for 2–20 accounts on a single home server; weakens transactional clarity and backup simplicity.
- **Move legacy files into subfolders without separating SQL/domain rules**: Leaves coupling intact and fails to enforce explicit `UserScope`.

## Consequences & Revisit Triggers

- Revisit adding a separate worker process (from the same image) or external queue **only** when measured job durations starve interactive web latency despite SQLite WAL + short transactions.
- Introduce a frontend UI framework only via a follow-up ADR tied to concrete client state-management bottlenecks.
