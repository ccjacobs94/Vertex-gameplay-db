# Contributor Guide (Rebuilt Architecture)

## Local Setup (Clean Clone)

1. Ensure Python 3.12+ and Docker are available (Node 22+ is used only for building `frontend/dist/`).
2. During staged replacement (`Phases 0–5`), legacy checks run via:
   ```bash
   scripts/check.sh
   ```
   All checks execute inside the container with `--network none`, read-only source mount, and throwaway temp databases. Never point tests at live `data/` or `data-qa/`.
3. Run the new modular architecture's test suites locally with `PYTHONPATH=src`:
   ```bash
   PYTHONPATH=src python3 -m unittest discover -s tests/unit
   PYTHONPATH=src python3 -m unittest discover -s tests/integration
   PYTHONPATH=src python3 -m unittest discover -s tests/migration
   PYTHONPATH=src python3 -m unittest discover -s tests/contracts
   ```
4. Build modular frontend assets & regenerate OpenAPI TypeScript types offline:
   ```bash
   PYTHONPATH=src python3 scripts/export_openapi.py
   PYTHONPATH=src python3 scripts/build_frontend.py
   ```

## Synthetic Demo Data

Use the CLI or synthetic test fixtures (`tests/migration/legacy_fixtures.py`) to initialize disposable local state:

```bash
export VERTEX_DATA_DIR=/tmp/vertex-dev
export VERTEX_SECRET_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
PYTHONPATH=src python3 -m vertex migrate
PYTHONPATH=src python3 -m vertex bootstrap-admin --username admin --password "ChangeMeNow!123"
PYTHONPATH=src python3 -m vertex serve
```

Never commit `.env`, `*.db`, `data/`, or real platform API keys.

## Worked Example: Adding a Platform Adapter

1. **Create an adapter** in `src/vertex/integrations/platforms/<provider>.py` implementing `PlatformAdapter` (`capabilities()`, `fetch_library(credentials, client)`), returning `list[PlatformGameSnapshot]` and raising `PlatformAdapterError(code=..., retryable=...)` with sanitized error messages.
2. **Wire credentials** in `src/vertex/identity/models.py` (`ConnectionSecrets` / `PublicConnectionSummary`) without exposing secret fields in public GET models.
3. **Register sync execution** in `src/vertex/integrations/platforms/sync_service.py` so `JobRunner` dispatches `<provider>_sync` jobs -> calls `library.apply_platform_snapshot(scope, ...)` + `activity.record_observation(scope, ...)`.
4. **Never mutate personal fields directly**: Sync results must go through `library` and `activity` services so `status_locked`, `ownership_locked`, `user_rating`, `notes`, `favorite`, `provider_baselines`, and merge-only taxonomy rules remain intact.
5. **Add offline fixture tests** in `tests/unit/` and `tests/integration/` covering pagination, timeouts, retry backoff, replay idempotency, and edit preservation.

## Module Ownership & Rules

- Keep domain services (`identity`, `catalog`, `library`, `activity`) free of `fastapi` and `httpx` imports.
- Every personal service method requires an explicit `UserScope`.
- Follow Conventional Commits (`<type>(<scope>): <summary>`). Wait for Phase 6 cutover approval before altering legacy deployment hooks/scripts covered by `tests/test_cicd.py`.
