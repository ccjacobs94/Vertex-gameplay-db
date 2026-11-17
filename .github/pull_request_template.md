## Summary

Explain what this PR changes and why.

## Architectural & Safety Checklist

- [ ] Domain packages (`identity`, `catalog`, `library`, `activity`, `analytics`, `wishlist`) remain free of `fastapi` and `httpx` imports.
- [ ] User-owned edits (`status_locked`, `ownership_locked`, `user_rating`, `notes`, `favorite`, `is_locked` sessions, merge-only genres/tags) are preserved by automated paths.
- [ ] Offline test coverage added (`tests/unit`, `tests/integration`, `tests/migration`, `tests/contracts`, `tests/e2e`) using mocked transports and temporary databases only.
- [ ] OpenAPI TypeScript types (`scripts/export_openapi.py`) and frontend build (`scripts/build_frontend.py`) are clean (`git diff --exit-code frontend/src/shared/api-types.ts`).
- [ ] `scripts/check.sh` passes before push/merge (no secrets, DB files, or deployment regressions).
