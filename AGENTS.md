# Game Hub — Project Guidelines

Game Hub is a self-hosted, multi-platform game library tracker and MCP server running in Docker on this Linux home server. It aggregates games, playtime, achievements, and backlog statuses across **PC (Steam)**, **Xbox / PC Game Pass**, **PlayStation 4 / 5**, and **Nintendo Switch**.

## Architecture & Key Files

- `app.py`: FastAPI web server (port `8090` inside container, mapped to `8095` on the host) and background auto-sync scheduler.
- `db.py`: SQLite database layer (`data/games.db` on the host, `/app/data/games.db` inside the container).
- `sync_engine.py`: Automated sync connectors for Steam Web API, Xbox (`xbl.io`), PSN (`psnawp`), plus keyless game catalog & cover art lookup (Steam Store Search + Wikipedia API).
- `mcp_server.py`: Model Context Protocol (MCP) server exposing library metrics, backlog search, recommendations, and status updates to AI agents.
- `static/`: Responsive web frontend (`index.html`, `styles.css`, `app.js`).
- `tests/`: `unittest` suite. `test_invariants.py` guards the data-preservation rules below (see Agent Development Model).
- `scripts/check.sh`: Quality gate (branch policy, secrets, syntax, PWA cache bump, container test run). Must pass before every commit and merge.
- `.githooks/`: Versioned git hooks that enforce GitFlow and the gate locally.

## Endpoints & Operations

- **Local Web UI URL**: `http://localhost:8095` (or `http://<your-lan-or-tailscale-ip>:8095`)
- **Streamable HTTP MCP URL**: `http://localhost:8097/mcp`
- **Rebuild & Restart Container**:
  ```bash
  docker compose up -d --build
  ```
- **Trigger Manual Sync via CLI**:
  ```bash
  curl -X POST http://localhost:8095/api/sync
  ```
- **Preserve User Edits**: When modifying `db.py` or `sync_engine.py`, never overwrite a game's `completion_status` during automated syncs if `status_locked = 1`, and never overwrite `user_rating`, `notes`, or `favorite`.
- **Genres & Tags**: Genres are broad categories, tags are mechanics/modes/themes (see `taxonomy.py`). Enrichment and syncs merge, never replace: discovered genres/tags are added to the existing lists, existing entries are never dropped. The `update_game_genres` MCP tool defaults to merge mode for the same reason. Labels outside the controlled taxonomy are ignored, never written silently.

---

# Agent Development Model

Every change, whether feature, fix, or refactor, follows this model. It's built to prevent regressions, scope creep, and data loss on a live home-server deployment. **The quality gate (`scripts/check.sh`) is the source of truth**: if it fails, the work isn't done, no matter how good the change looks.

## Non-Negotiables

1. **Never break an invariant test.** `tests/test_invariants.py` encodes the "Preserve User Edits" and "Genres & Tags" rules above. Never delete, skip, weaken, or edit these tests to make a change pass. Changing one needs the user's explicit approval *and* a matching update to this file in the same PR.
2. **Never touch live data from tests or experiments.** Tests must use `tempfile` databases via the `db_path=` argument. Never point code under test at `data/games.db`. Before running any migration or bulk script against the live DB, back it up: `cp data/games.db data/games.db.bak-$(date +%Y%m%d-%H%M%S)`.
3. **Never commit secrets or data.** That means `.env`, `.agents/plugins/*/mcp_config.json`, `data/`, and `*.db*`. The gate checks this.
4. **Never commit directly to `main` or `develop`.** See GitFlow below.
5. **Never push, merge to `main`, tag, or deploy without the user's explicit approval.**

## The Change Lifecycle

```
0. Orient → 1. Baseline → 2. Branch → 3. Test first → 4. Implement → 5. Gate → 6. Self-review → 7. Commit → 8. Integrate → 9. Verify
```

### 0. Orient
- Read the relevant code paths end-to-end before editing, including every **consumer** of what you change. A `db.py` change can affect `app.py`, `mcp_server.py`, `sync_engine.py`, `static/app.js`, and `.agents/skills/game-advisor/SKILL.md`.
- If the request is ambiguous or conflicts with a rule in this file, ask before writing code.

### 1. Baseline
- Start from a clean tree (`git status` is empty) and run `scripts/check.sh` on the base branch **before** changing anything. If the baseline is already red, stop and report it. Don't build on a broken base, and don't "fix" unrelated failures inside a feature branch.

### 2. Branch
- Create the correct GitFlow branch (see below). One branch = one logical change.

### 3. Test First
- **Bug fix:** first write a test that reproduces the bug and **fails**, then fix it. Add a comment naming the bug or date (see the Portal 2 example in `tests/test_taxonomy.py`).
- **Feature:** write tests for the new behaviour, including at least one edge case (empty input, missing optional field, reconciled multi-platform group, locked status).
- **New user-owned field:** if a feature adds a field the user edits (like `user_rating`/`notes`), add it to the preservation tests in `tests/test_invariants.py` and to the "Preserve User Edits" rule above.
- Tests use `unittest` and live in `tests/test_<area>.py`. External APIs (Steam, xbl.io, PSN, Wikipedia) must be mocked. Tests never make network calls; the gate runs them with `--network none`.

### 4. Implement
- **Minimal diff.** Change only what the task needs. No drive-by refactors, renames, reformatting, or dependency bumps. Propose those as separate branches.
- **Preserve unrelated comments and docstrings.**
- **Backwards-compatible contracts.** Existing REST response fields (`/api/*`) and MCP tool names/parameters can be *added to* but not renamed, removed, or retyped. If a breaking change is truly needed, update every consumer in the same branch and call it out in the commit (`feat!:`).
- **Schema changes** are additive and idempotent only. Add columns via the guarded `ALTER TABLE ... ADD COLUMN` pattern in `db.init_db()`, which runs on every container start. Never `DROP`, rename, or retype existing columns, and never rewrite existing rows in a migration without user approval.
- **Sync / enrichment code** must merge, never replace, and must respect `status_locked`. Use `COALESCE`/`MAX` patterns like `upsert_synced_game()`.
- **Frontend** (`static/`): any change to a cached asset requires bumping `CACHE_NAME` in `static/sw.js` (`vertex-pwa-vN` → `vN+1`), or installed PWAs will serve stale code. The gate enforces this.
- **Dependencies:** pin a minimum version in `requirements.txt`, then rebuild the test image (`docker compose build`) so the gate tests against it.

### 5. Gate
Run the full gate and make sure it ends with `Quality gate PASSED`:
```bash
scripts/check.sh
```
It checks GitFlow branch naming, that no secrets or DB files are tracked, Python and JS syntax, the PWA cache bump, and the full `unittest` suite inside the app image (network-isolated, read-only source, throwaway DB). Never commit with `--no-verify`.

### 6. Self-Review
Before committing, read your own `git diff develop...HEAD` (plus unstaged changes) as a reviewer would and confirm:
- [ ] Every changed line is needed for the task. Nothing unrelated, no leftover debug prints or commented-out code.
- [ ] Every consumer of a changed function, endpoint, or MCP tool still works.
- [ ] Error paths are handled. A failing external API must not abort the whole sync or corrupt rows.
- [ ] No user edit (`completion_status` when locked, `user_rating`, `notes`, `favorite`, genres/tags) can be overwritten by an automated path.
- [ ] Docs are updated where behaviour changed: this file, `README.md`, `.env.example` for new env vars, `SKILL.md` for MCP changes.

### 7. Commit
- [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <imperative summary>` (≤72 chars).
  Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `build`, `ci`, `style`, `revert`. Use `!` for breaking changes.
  Scopes: `db`, `sync`, `mcp`, `api`, `ui`, `pwa`, `auth`, `taxonomy`, `achievements`, `analytics`, `infra`.
- Small, atomic commits. Each one should pass the gate on its own so `git bisect` stays useful.
- The body explains **why**, and lists any follow-ups or known limitations.

### 8. Integrate
See the GitFlow merge procedure below. Re-run the gate on the target branch **after** merging.

### 9. Verify (Deploy Smoke Test)
Only on user request, after merging to `main`:
```bash
cp data/games.db data/games.db.bak-$(date +%Y%m%d-%H%M%S)
docker compose up -d --build
docker compose logs --tail=50 game-hub          # no tracebacks
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:8095/   # expect 200 or a 30x redirect to /login
```
Then manually exercise the feature you changed in the Web UI. If anything regresses, roll back right away (see Hotfix / Rollback).

## Definition of Done
A change is done only when **all** of these hold:
1. `scripts/check.sh` passes on the feature branch and again on `develop` after the merge.
2. New or changed behaviour is covered by tests, and bug fixes include a regression test that failed before the fix.
3. The invariant tests are untouched, or the user approved the change.
4. Docs and `.env.example` are updated where needed.
5. The history is clean, uses conventional commits, and is merged with `--no-ff`.
6. A short summary was reported to the user: what changed, tests added, gate result, and anything that needs their approval (push, release, deploy).

---

# GitFlow

## Branches

| Branch | Purpose | Branches from | Merges into | Who may merge |
|---|---|---|---|---|
| `main` | Production. Every commit is a tagged release running in Docker. | — | — | User approval required |
| `develop` | Integration branch for the next release. Always green. | `main` (once) | `release/*` | Agent, after the gate passes |
| `feature/<kebab-name>` | New functionality | `develop` | `develop` | Agent, after the gate passes |
| `bugfix/<kebab-name>` | Non-urgent fix for something on `develop` | `develop` | `develop` | Agent, after the gate passes |
| `release/<X.Y.Z>` | Release stabilization: version bump, docs, fixes only | `develop` | `main` **and** `develop` | User approval required |
| `hotfix/<X.Y.Z>` | Urgent fix for production | `main` | `main` **and** `develop` | User approval required |

Branch names are lowercase kebab-case, e.g. `feature/steam-wishlist-import`, `bugfix/psn-token-refresh`. Legacy `feat/*` branches predate this model. Don't create new ones.

Versioning follows [SemVer](https://semver.org/) with annotated tags `vX.Y.Z` on `main`: MAJOR for breaking API/MCP/schema changes, MINOR for features, PATCH for fixes.

## Procedures

**Start a feature / bugfix**
```bash
git switch develop && git pull --ff-only     # skip pull if offline / no remote develop
scripts/check.sh                             # baseline must be green
git switch -c feature/<name>
```

**Keep a long-running branch current** (if `develop` has moved):
```bash
git fetch && git rebase develop              # only for unpushed branches; otherwise: git merge develop
scripts/check.sh
```

**Finish a feature / bugfix** (agent may do this once the gate is green):
```bash
scripts/check.sh                              # on the feature branch
git switch develop
git merge --no-ff feature/<name> -m "Merge branch 'feature/<name>' into develop"
scripts/check.sh                              # on develop, post-merge
git branch -d feature/<name>
```
If the post-merge gate fails, undo the merge right away (`git reset --hard ORIG_HEAD`, only if not yet pushed), fix it on the feature branch, and try again.

**Release** (user approval required):
```bash
git switch -c release/X.Y.Z develop
# only: version bump, changelog/README, release-blocking fixes; then gate
git switch main    && git merge --no-ff release/X.Y.Z && git tag -a vX.Y.Z -m "vX.Y.Z"
git switch develop && git merge --no-ff release/X.Y.Z
git branch -d release/X.Y.Z
# push + deploy only after the user approves: git push origin main develop --tags
```

**Hotfix / Rollback** (user approval required):
```bash
git switch -c hotfix/X.Y.Z main               # failing regression test first, then fix, then gate
git switch main    && git merge --no-ff hotfix/X.Y.Z && git tag -a vX.Y.Z -m "vX.Y.Z"
git switch develop && git merge --no-ff hotfix/X.Y.Z
```
For an immediate rollback of a bad deploy: `git switch --detach v<previous>` → `docker compose up -d --build`, restore the DB backup if a migration ran, then fix forward with a hotfix.

## Rules
- Never `git push --force` to `main` or `develop`. Never rewrite pushed history.
- Never merge a branch whose gate is red. Never use `--no-verify`.
- Never mix unrelated changes in one branch. Split them.
- Never resolve merge conflicts by discarding the other side's changes without understanding them. Re-run the gate after every conflict resolution.
- Ask before deleting any branch you didn't create in the current task.

## Local Enforcement (Git Hooks)
Versioned hooks in `.githooks/` back up these rules. Enable once per clone with `git config core.hooksPath .githooks`:
- `pre-commit`: blocks direct commits on `main`/`develop` and runs `scripts/check.sh --quick`.
- `commit-msg`: enforces the Conventional Commits format.
- `pre-push`: runs the full `scripts/check.sh`, including container tests.

