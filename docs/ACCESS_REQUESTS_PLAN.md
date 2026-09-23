# Access gate: corp/alliance allowlist and access requests – implementation plan

Status: implemented · 2026-09-23

Goal: a character that isn't registered yet can log in via EVE SSO. If its
corporation or alliance is on an admin-maintained allowlist, an **access
request** appears in the Admin tool. The admin approves (choosing tool grants)
or rejects it. Approval creates a new, dedicated tenant for that character.
Registered characters are re-checked against the allowlist so that leaving an
allowed corp/alliance suspends access.

This does **not** bring back the retired corp/alliance registry entries
(`admin_schema.sql:17-23`). The allowlist only decides **who may ask**; access
itself stays per character, one tenant per character, with explicit tool
grants. `tenant_registry_entries` stays character-only.

---

## 1. Behaviour summary

| Situation | Result |
|---|---|
| Unregistered, corp/alliance **not** allowlisted | `?gate=denied` (as today) |
| Unregistered, corp/alliance allowlisted, no request yet | pending request created → `?gate=pending`, no session cookie |
| Unregistered, request pending | `?gate=pending` (request refreshed: name, corp, alliance, `last_login_at`) |
| Unregistered, request rejected | `?gate=rejected`; stays blocked until the admin deletes the rejection (decision 2) |
| Registered, affiliation still allowed, or allowlist empty, or has `admin` grant | normal login (as today) |
| Registered, affiliation no longer allowed | `?gate=suspended`, no cookie; tenant and data untouched (decisions 3a, B) |
| ESI affiliation lookup fails | use the last stored affiliation if ≤ 7 days old, otherwise deny with `?gate=error` (decision 3e) |

Rules:
- Re-check applies to **every** registered character (decision 3b) **except**
  characters holding the `admin` grant (decision 3c).
- **No re-check while the allowlist is empty** (decision 3d) – deploying this
  changes nothing until the first corp/alliance is added.
- During a session, the middleware re-checks lazily when the last check is
  older than **6 hours** (decisions 3f–3h). No scheduler dependency.
- Removing an allowlist entry leaves pending requests from it open, flagged
  "no longer allowlisted" (decision A).
- Characters cannot withdraw their own request (decision 5).
- Admins see a pending-request count badge on the Admin card and in QuickNav
  (decision 4).
- Gate disabled (`access_gate_enabled = false`): none of this applies.

---

## 2. Schema (`docs/admin_schema.sql`, all unscoped, no RLS)

`admin_schema.sql` is already in every schema loop (`deploy/deploy.sh`,
`README.md`, `deploy/README.md`, `.cursor/start.sh`) – **no new schema file**.

```sql
-- Corporations/alliances whose members may request access.
CREATE TABLE IF NOT EXISTS access_allowlist (
    entry_type TEXT NOT NULL CHECK (entry_type IN ('corporation', 'alliance')),
    entry_id BIGINT NOT NULL,
    name TEXT NOT NULL,                       -- cached at add time
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by_character_id BIGINT,
    PRIMARY KEY (entry_type, entry_id)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON access_allowlist TO eve_trader_app;

-- One row per character that ever requested access.
CREATE TABLE IF NOT EXISTS access_requests (
    character_id BIGINT PRIMARY KEY,
    character_name TEXT NOT NULL,
    corporation_id BIGINT NOT NULL,
    corporation_name TEXT,
    alliance_id BIGINT,
    alliance_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    decided_by_character_id BIGINT
);
CREATE INDEX IF NOT EXISTS access_requests_status_idx ON access_requests (status);
GRANT SELECT, INSERT, UPDATE, DELETE ON access_requests TO eve_trader_app;

-- Affiliation state on the existing character registry.
ALTER TABLE tenant_registry_entries ADD COLUMN IF NOT EXISTS corporation_id BIGINT;
ALTER TABLE tenant_registry_entries ADD COLUMN IF NOT EXISTS alliance_id BIGINT;
ALTER TABLE tenant_registry_entries ADD COLUMN IF NOT EXISTS affiliation_checked_at TIMESTAMPTZ;
ALTER TABLE tenant_registry_entries ADD COLUMN IF NOT EXISTS access_suspended BOOLEAN NOT NULL DEFAULT false;
```

`approved` rows are kept as an audit trail (who approved, when). Deleting a
`rejected` row is what re-opens the door for that character.

`connect_unscoped()`'s docstring and CLAUDE.md's list of unscoped tables get
`access_allowlist` and `access_requests` added.

---

## 3. ESI client (`eve_trader/esi_client.py`)

- **new** `character_affiliation(character_ids: list[int]) -> dict[int, tuple[int, Optional[int]]]`
  – `POST /characters/affiliation/`, public, up to 1000 ids per call, returns
  `{character_id: (corporation_id, alliance_id)}`. **Not cached** – the whole
  point is a fresh answer (`character_public_info` is cached and can be stale
  after a corp change).
- **new** `search_corporations_alliances(name) -> list[{type, id, name}]` –
  reuses `_post_universe_ids`, reading the `corporations` and `alliances`
  sections (exact match, like `character_search`).
- Names for display: the existing `resolve_names(ids)` (`/universe/names/`).

Both are public endpoints – no token involved.

---

## 4. New module `eve_trader/access_policy.py`

Pure policy logic, no FastAPI, reused by the callback, the middleware, the
status endpoint and the admin actions.

```python
AFFILIATION_RECHECK_SECONDS = 6 * 3600
AFFILIATION_STALE_LIMIT_SECONDS = 7 * 24 * 3600

def is_allowed(corporation_id, alliance_id) -> bool
    # True if either id is in access_allowlist

def allowlist_active() -> bool
    # False while access_allowlist is empty (decision 3d)

def fetch_affiliation(character_id) -> Optional[tuple[int, Optional[int]]]
    # ESI call; None on ESIError/timeout (short timeout, e.g. 5 s)

def evaluate_registered(character_id, tool_keys, stored) -> Verdict
    # ok | suspended | unknown, applying:
    #   admin grant -> ok; allowlist empty -> ok;
    #   fresh fetch -> compare; fetch failed -> stored if <= 7 days old, else unknown

def refresh_registered(character_id, tool_keys, force=False) -> Verdict
    # single-flight per character_id (lock dict), skips if checked < 6 h ago
    # unless force; writes corporation_id/alliance_id/affiliation_checked_at/
    # access_suspended on the registry row
```

Constants live here, not in config, since they are security policy, not
per-tenant settings.

---

## 5. Storage (`eve_trader/storage.py`, all via `connect_unscoped()`)

| Function | Notes |
|---|---|
| `list_allowlist()`, `add_allowlist_entry(type, id, name, by)`, `remove_allowlist_entry(type, id)`, `allowlist_is_empty()` | |
| `allowlist_contains(corporation_id, alliance_id) -> bool` | one query |
| `get_access_request(character_id)`, `upsert_pending_access_request(...)` | upsert keeps `status` if already `rejected`/`approved`; refreshes name/affiliation/`last_login_at` |
| `list_access_requests(status=None)` | joins `access_allowlist` to flag "no longer allowlisted" (decision A) |
| `count_pending_access_requests()` | for the badge |
| `reject_access_request(character_id, by)`, `delete_access_request(character_id)` | |
| `approve_access_request(character_id, tool_keys, by) -> tenant_id` | **one transaction**: `INSERT tenants` → `INSERT tenant_registry_entries` (with the stored affiliation, `affiliation_checked_at = now()`) → `INSERT tool_grants` → mark request `approved`. Fails cleanly if the character is already registered (e.g. added via "Add User" in the meantime). |
| `update_registry_affiliation(character_id, corp, alliance, suspended)` | |
| `session_authorization(...)` | **extended**, still one query: additionally returns `corporation_id`, `alliance_id`, `affiliation_checked_at`, `access_suspended` and `EXISTS(SELECT 1 FROM access_allowlist)`. Keeps the "one DB read per request" property. |
| `list_users_with_grants()` | adds corp/alliance ids, `affiliation_checked_at`, `access_suspended` |

---

## 6. Login callback (`api/routers/auth.py`, gate branch at 250–272)

```
tenant_id = storage.resolve_tenant_id(character_id)
if tenant_id is not None:
    tool_keys = storage.list_tool_grants_for_character(character_id)
    verdict = access_policy.refresh_registered(character_id, tool_keys, force=True)
    if verdict == suspended: redirect ?gate=suspended (no cookie)
    if verdict == unknown:   redirect ?gate=error&message=affiliation_unavailable
    -> existing path: refresh name, set cookie, ?gate=success
else:
    req = storage.get_access_request(character_id)
    if req and req.status == 'rejected': redirect ?gate=rejected
    aff = access_policy.fetch_affiliation(character_id)
    if aff is None: redirect ?gate=error&message=affiliation_unavailable
    if not access_policy.is_allowed(*aff): redirect ?gate=denied
    storage.upsert_pending_access_request(character_id, character_name, *aff, names…)
    redirect ?gate=pending
```

- `force=True` at login: always a fresh check, independent of the 6 h window.
- The request is only created **after** SSO has verified the identity, so
  nobody can file a request for someone else.
- The callback is a sync route (runs in the threadpool), so the ESI call there
  doesn't block the event loop.

---

## 7. Middleware (`api/app.py`, `AccessGateMiddleware.dispatch`)

After `authorize_session_cookie` succeeds:

1. `session.access_suspended` → `403 {"detail": "access_suspended"}`.
2. If not admin, allowlist active, and `affiliation_checked_at` older than 6 h:
   run `access_policy.refresh_registered(...)` via
   `await run_in_threadpool(...)`. **Required**, because `dispatch` is
   `async` – a blocking ESI call directly in it would stall every request on
   the worker. Single-flight per character, so parallel requests from one
   browser trigger one ESI call.
3. Verdict `suspended` → 403 `access_suspended`. Verdict `unknown` (ESI down
   and stored affiliation older than 7 days) → 403 `access_unverifiable`.
4. Otherwise continue as today.

Suspension is a **registry flag**, not a session revocation: when the
character rejoins an allowed corp, the next check (login or `/gate/status`)
clears the flag and the existing cookie works again. Achieves the same effect
as revoking (no access while suspended), plus automatic recovery.

`AuthorizedSession` gets `access_suspended`, `corporation_id`, `alliance_id`,
`affiliation_checked_at`, `allowlist_active`.

---

## 8. Gate status (`api/routers/gate.py`)

`/api/gate/status` (sync, exempt) runs the same lazy re-check and returns
additionally:
- `suspended: bool` – drives the "access suspended" message on Landing
- `pending_access_requests: int | null` – only when the session holds the
  `admin` grant (drives the badge; `null` for everyone else, so the count is
  never exposed to non-admins)

Landing and middleware keep using the same `authorize_session_cookie` +
`access_policy` chokepoint, so they cannot disagree (existing CLAUDE.md
principle).

---

## 9. Admin actions (`eve_trader/admin.py`) and routes (`api/routers/admin.py`)

| Action | Route |
|---|---|
| `do_list_allowlist()` | `GET /api/admin/allowlist` |
| `do_search_allowlist_candidates(name)` | `GET /api/admin/allowlist/search?q=` |
| `do_add_allowlist_entry(entry_type, entry_id)` – resolves and caches the name | `POST /api/admin/allowlist` |
| `do_remove_allowlist_entry(entry_type, entry_id)` | `DELETE /api/admin/allowlist/{entry_type}/{entry_id}` |
| `do_allowlist_impact(entry_type, entry_id, action)` – which registered non-admin users would be suspended if this entry is added (first entry) or removed | `GET /api/admin/allowlist/impact` |
| `do_refresh_user_affiliations()` – one bulk affiliation call for all registered characters, updates the registry (also feeds the impact preview) | `POST /api/admin/users/refresh-affiliations` |
| `do_list_access_requests(status)` | `GET /api/admin/access-requests?status=` |
| `do_approve_access_request(character_id, tool_keys)` – validates `tool_keys ⊆ ALL_TOOL_KEYS` | `POST /api/admin/access-requests/{character_id}/approve` |
| `do_reject_access_request(character_id)` | `POST /api/admin/access-requests/{character_id}/reject` |
| `do_delete_access_request(character_id)` – re-opens the door after a rejection | `DELETE /api/admin/access-requests/{character_id}` |
| `do_count_pending_access_requests()` | `GET /api/admin/access-requests/count` |

`decided_by_character_id` / `added_by_character_id` come from the admin's
session. All routes sit under `/api/admin/` and are covered by the existing
`admin` grant; `tests/test_gate_route_coverage.py` needs no new bucket.

---

## 10. Frontend

| File | Change |
|---|---|
| `App.tsx` (`AuthRedirectHandler`) | new `gate` values: `pending` ("Request submitted – an admin will review it"), `rejected`, `suspended` ("Access suspended – your corporation/alliance is no longer allowlisted"); reword `denied` to "Your corporation/alliance is not allowlisted"; `error` with `affiliation_unavailable` → "Couldn't verify your corporation, try again later" |
| `api/client.ts` | `gateWasDeniedThisLoad` logic also covers `pending`/`rejected`/`suspended` (same redirect-loop protection) |
| `pages/Landing.tsx` | show the suspended state from `/gate/status`; pending badge on the Admin card |
| `components/QuickNav.tsx` | pending badge on the Admin entry |
| `pages/admin/AdminPage.tsx` | three changes: **Allowlist** section (search corp/alliance, add, remove; remove/first-add shows the impact preview and warns if the admin's own corp/alliance is affected, even though admins are exempt); **Access requests** section (pending table: character, corp, alliance, requested/last login, "no longer allowlisted" flag; Approve opens a dialog with the same tool checkboxes as the user table incl. the `characters` auto-tick; Reject; a collapsed "Rejected" list with Delete); **Users** table gains corp, alliance, last check, suspended badge, plus a "Refresh affiliations" button |

---

## 11. Tests

- **Storage:** allowlist CRUD; `allowlist_contains` for corp and alliance
  match; upsert keeps `rejected`; `approve_access_request` is atomic (failure
  in grants leaves no tenant/registry row) and refuses an already-registered
  character; `session_authorization` returns the new fields in one query.
- **Policy:** admin exempt; empty allowlist = no check; fresh fetch
  allowed/not allowed; ESI failure with stored ≤ 7 days → stored verdict;
  > 7 days → unknown; 6 h window respected, `force` bypasses it;
  single-flight (two concurrent calls, one ESI call).
- **Callback:** every row of the table in section 1, with ESI mocked; no
  cookie on pending/rejected/denied/suspended; request created only for
  allowlisted affiliation.
- **Middleware:** suspended → 403; stale check triggers refresh via threadpool;
  unknown → 403; admin never re-checked; gate off unaffected.
- **Gate status:** `suspended` flag; `pending_access_requests` only for admins.
- **Admin:** approve creates tenant named after the character with exactly the
  chosen grants; invalid tool key rejected; reject/delete cycle re-enables a
  new request; impact preview lists the right users; "no longer allowlisted"
  flag.
- **Frontend:** Playwright check of the pending/rejected/suspended messages and
  the Admin sections (per CLAUDE.md, script and screenshots deleted after).

---

## 12. Docs

- **CLAUDE.md**, "Tool permissions & Admin": allowlist decides who may request,
  not who gets access; registry stays character-only; re-check rules (admin
  exempt, empty allowlist = off, 6 h lazy, 7 d stale limit); suspension is a
  registry flag; new unscoped tables.
- `docs/OPERATOR_SECURITY.md`: recommended rollout (deploy → "Refresh
  affiliations" → check impact preview → add allowlist entries); what happens
  on ESI outages; admins are exempt from the re-check, so keep the `admin`
  grant list short.
- `README.md` user-management section: the new request flow next to
  "Add User".

---

## 13. Order / PR split

| # | Scope | Depends on |
|---|---|---|
| 1 | Schema, ESI client methods, storage functions, `access_policy` module + tests | – |
| 2 | Request flow: callback changes (pending/rejected/denied), admin allowlist + request actions/routes, Admin UI sections, App.tsx messages | 1 |
| 3 | Re-check: callback for registered characters, middleware lazy check, `/gate/status` fields, suspended state, users-table columns, refresh button, impact preview | 1 |
| 4 | Badges (Landing, QuickNav) | 2 |
| 5 | Docs | 2, 3 |

---

## Decision log

| # | Decision |
|---|---|
| Base | Corp/alliance allowlist in its own global table; registry stays character-only; allowlisted → pending request, else denied |
| 1 | Admin picks tool grants in the approve dialog; approval creates tenant + registry + grants |
| 2 | Rejected characters stay blocked until the admin deletes the rejection |
| 3a | Affiliation re-checked at every login; failing it suspends access, data kept |
| 3b | Re-check applies to all users, including "Add User" ones… |
| 3c | …except characters with the `admin` grant |
| 3d | No re-check while the allowlist is empty |
| 3e | ESI failure: fall back to the stored affiliation up to 7 days old, else deny |
| 3f–h | Lazy re-check in the middleware when the last check is older than 6 hours; no scheduler |
| 4 | Pending-request count badge on the Admin card and in QuickNav, admins only |
| 5 | Characters can't withdraw their own request |
| A | Removing an allowlist entry leaves its pending requests open, flagged |
| B | Suspended access has its own message (`?gate=suspended`); Admin user list shows corp, alliance, last check, status |
